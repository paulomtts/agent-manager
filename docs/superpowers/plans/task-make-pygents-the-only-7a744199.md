<!-- task-pipeline: validated -->
# Make pygents the only engine (card 7a744199)

Subtask of story 1ec08da2 "Switch over", milestone 84c3b532. Narrows plan Task 5.3 (`docs/superpowers/plans/2026-09-25-pygents-engine.md` lines 1342-1352) and design decision G7 (`docs/superpowers/specs/2026-09-25-pygents-engine-design.md` lines 100-103): the last subtask flips the default and deletes `engine.py`, `workflow/loader.py`, `workflow/registry.py`, `builtin/*.yaml`, the three resume helpers and the flag. Starts from the tip of sibling a300ab2b.

This is a pure removal and relocation. No new behaviour, no new test logic: the pygents half of every parametrised test is the specification.

## Scope

### 1. Relocate into `src/agent_manager/runtime/`

Move these out of `src/agent_manager/engine.py` unchanged: `SubtaskSummary`, `subtask_context`, `bind_arguments`, `run_one_step`, `_document_paths`, `_escalate`, `_stop`, `_record_subtask_status` and `RESERVED_CONTEXT_KEYS`. Also move every private helper they, or the runtime modules, still reach through `old.*` / `old_engine.*`. The current tree reaches `_utcnow` (`runtime/engine.py`, `runtime/checkpoint.py`) and `_render_error` (`runtime/engine.py`, `runtime/compile.py`), plus whatever `run_one_step` calls transitively (for example `_bind_result`, which `runtime/context.py`'s docstring names). Move each body verbatim.

- Put the helpers in a runtime module that does not import pygents, so that `dispatch.py` can import `bind_arguments` without pulling in pygents (rule 1). The plan picks the module name, for example `runtime/subtask.py`.
- `_bind_result` moves too, but not because `run_one_step` calls it -- it does not. It is called only from `run_subtask` (the yaml-only walk, deleted whole) and from `tests/test_engine.py::test_a_phase_named_base_branch_cannot_overwrite_the_alias`, which calls `engine._bind_result` directly and is not part of any `yaml`/`pygents` parametrisation, so it survives the test-file edits below unchanged apart from its import. Move it so that test keeps passing against the new location; do not go looking for a live production caller in the pygents walk, because `runtime/context.py`'s `binding_table` is an independent reimplementation ("the pygents twin"), not a caller of this function.
- `EngineError` moves from `src/agent_manager/errors.py` into `runtime/`. It goes in a module that imports neither pygents nor `prompt`/`dispatch`, so `prompt.py`, `dispatch.py` and `results.py` can import the plain class without an import cycle. `runtime/__init__.py` must stay import-free so that importing that module does not load pygents. `AgentPhaseFailed` stays in `errors.py`. Rewrite the `errors.py` module docstring (lines 3-5 explain why `EngineError` lives there) and the `engine.AgentPhaseRunner` reference (line 46) so they no longer mention `engine.py`.
- Every `from agent_manager import engine as old` / `old_engine` / `yaml_engine`, and every `from agent_manager.engine import ...`, becomes a direct reference to the new runtime location. That covers `runtime/engine.py`, `runtime/compile.py`, `runtime/checkpoint.py`, `runtime/context.py`, `dispatch.py` (`engine.bind_arguments`), `integration.py` (`engine.SubtaskSummary` in annotations) and `cli.py`.

### 2. Delete

- `src/agent_manager/engine.py`, `src/agent_manager/workflow/loader.py`, `src/agent_manager/workflow/registry.py` and `src/agent_manager/workflow/builtin/` (both `task.yaml` and `integrate.yaml`).
- `phases.from_loader` (`workflow/phases.py:153`), including its lazy `workflow.loader` import. In `workflow/task.py`, rewrite the module docstring's first paragraph (lines 3-7), which cites `from_loader`, the deleted `builtin/task.yaml` and the deleted `tests/workflow/test_declared.py` as the digest-pinning test -- not just the `from_loader` mention.
- `workflow/__init__.py` re-exports of `load_builtin`, `load_workflow`, `builtin_path`, `default_registry` and other loader/registry names. Also rewrite its docstring, which says "`load_builtin("task")` is the engine's entry point".
- `dispatch.py`: the `workflow.loader` import (line 41, both `AgentPhase` and `Workflow`), the `AnyAgentPhase` union (line 43, which collapses to `phase_model.AgentPhase`) and the by-name gate branch (lines 308-316, `if isinstance(entry, str)`). Only the declared callable path remains.
  - `evaluate_gates`'s `workflow: Workflow` parameter (line 289) and `AgentRunner.workflow: Workflow` field (line 406) exist only to resolve a by-name gate through `workflow.function(entry)`; a declared `phases.AgentPhase` always carries its gates as real callables (`workflow/phases.py:48,60`), so once the by-name branch is gone, `workflow` is read nowhere else in the file (`self.workflow` at line 556 was its only caller). Drop the parameter and the field, not just their type, and update `evaluate_gates`'s call site (line 556) to stop passing `self.workflow`.
  - That cascades to `cli.RunnerFactory.__call__` and `cli.default_runner_factory` (both take `workflow: Workflow`): drop the parameter there too. Drop the `workflow=` keyword from every call: `cli.drive_subtask`'s `factory(workflow=workflow, ...)` (~826) and `integration.py`'s `runner_factory(workflow=workflow, ...)` (~169). This is what makes deleting `workflow = load_builtin(WORKFLOW_NAME)` at cli.py:819 and `workflow = load_builtin(run.workflow)` at cli.py:1613 safe -- those bindings have no other reader once the keyword argument is gone; deleting the `load_builtin` call alone, without also dropping the parameter, leaves a `NameError` where `factory(workflow=workflow, ...)` still names it.
  - Every test that calls `dispatch.evaluate_gates(phase, some_workflow, values, warnings)` or constructs `dispatch.AgentRunner(workflow=..., ...)` drops that argument too, not only the by-name-path cases `tests/test_dispatch.py` already loses. Repoint or delete per the file's own tier rule below; do not leave a stale `workflow` positional argument in a survivor test.
- `prompt.py`: the `PromptPhase` Protocol docstring (lines 40-42) names both `AgentPhase` types. It now names only `workflow.phases.AgentPhase`, and any loader-only accommodation goes too.
- `cli.py`:
  - Delete `_skipped_origin` (476), `interrupted_phase` (504), `resume_start_phase` (545), `Engine` (687) and `ENGINES` (692).
  - Delete the `--engine` options on `am run` (~1240) and `am resume` (~1718), and the `engine` parameters threaded through `drive_subtask`, the run-card path and `resume_run`. `_check_engine` (~1118) exists solely to validate that flag (Typer's exit-2 refusal) and is called only from those two commands (~1185, ~1736); delete the function and both call sites with the flag.
  - Delete the `yaml_engine.run_subtask` branches (844, 1652) and the `workflow.loader` import (45).
  - Delete the `load_builtin` preflight calls (819, 889, 1491, 1613) together with the `workflow=` keyword argument they fed to `RunnerFactory`/`default_runner_factory` (see the `dispatch.py` bullet above) -- deleting the call alone and leaving the keyword leaves a `NameError`.
  - Delete the `WorkflowLoadError` import (46, `from agent_manager.workflow.registry import WorkflowLoadError`) and its entry in the `HANDLED` tuple (~1103): once `workflow/registry.py` is deleted, nothing raises it, and importing a class from a deleted module crashes at import time regardless of whether it is still reachable at runtime.
  - Delete the doc comments that describe the yaml/pygents split.
  - The resumable-status check (around line 431) takes its pygents value unconditionally: `("started", "stopped")`.
  - `WORKFLOW_NAME = "task"` stays, because it is the value recorded on the run row (G10).
- `orchestrate.py`: the `load_builtin` import (40) and preflight (570). Update the `engine._stop` docstring references (47, 53) to the new location. `tests/test_orchestrate.py::test_a_workflow_that_will_not_load_is_refused_before_anything_is_written` asserts this exact preflight raises `WorkflowLoadError`; delete it along with its now-dead `WorkflowLoadError` import (39) -- there is nothing left in this task's scope that raises that class.
- `integration.py`: the `yaml_engine` import (30), the `load_builtin` import and call (37, 160), the `engine: cli.Engine = "yaml"` parameter and its `cli.ENGINES` refusal, and the yaml branch at line 190. `runtime_engine.run_subtask(integrate_workflow.INTEGRATE, ...)` becomes unconditional. Update the module docstring's `engine.run_subtask` reference (12). The `workflow = load_builtin(WORKFLOW_NAME)` result (160) also stops feeding `runner_factory(workflow=workflow, ...)` (169): drop that keyword argument along with the `dispatch.py` parameter removal described above.
- `results.py` and `harness/registry.py`: update the docstring references to `engine._document_paths` and `workflow.registry.default_registry` so they point at code that still exists. Leave the imports alone except for the `EngineError` path.
- `pyproject.toml:10`: drop `pyyaml>=6.0.2`, because after the deletions nothing under `src/` or `tests/` imports `yaml`. Update `uv.lock` to match.

### 3. Out of scope

- The supervisor tree and a `pause()`-based stop.
- Exactly-once phases.
- Prompt benchmarking.
- Upstream pygents fixes. The `AgentRegistry._registry.pop` workaround in `runtime/engine.py` stays exactly as it is.
- `README.md` and the §6/§9 design-spec pointer, which belong to card e46098be. Update docstrings only where a deletion leaves them pointing at code that no longer exists.
- The critic-loop wiring (058981d3) and the real-harness parametrisation (a300ab2b), beyond collapsing their `engine` parameter as described below.

## Observable behaviour

- `am run --help` and `am resume --help` show no `--engine`. Passing `--engine` now fails with Typer's standard "No such option" usage error, exit code 2.
- Every run and every resume goes through `runtime.engine.run_subtask`. This includes stopped subtasks, which are now always resumable.
- Nothing changes byte for byte (rule 5 / G10) in:
  - the `SubtaskSummary` fields
  - journal lines
  - phase and attempt rows
  - escalation payloads
  - `run.workflow` values
  - JSON envelopes
- Error paths are unchanged:
  - A reserved context key, a gate failure or an invalid result file still raises and escalates as today.
  - The only difference is the module path of `EngineError`. The class, its message and its escalation rendering are the same.
- Rule 1 holds: only `src/agent_manager/runtime/` imports pygents, and `workflow/phases.py` does not.
- Rule 3 holds: nothing in the relocation changes how `agent.run()` is driven or when checkpoints happen.

## Tests

Tier names follow the design spec §14 "Testing" (`2026-09-23-agent-manager-design.md` lines 497-512). There is no other test-placement document. No new test is written. Every change below is a deletion, or a collapse of an `engine` parametrisation to its pygents case, with that case's assertions kept verbatim.

Delete whole files:

- `tests/workflow/test_loader.py`: pure-functions tier, and the loader is gone.
- `tests/workflow/test_registry.py`: pure-functions tier, and the registry is gone. It also imported `bind_arguments` and `EngineError` from their old paths.
- `tests/workflow/test_builtin_task.py`: pure-functions tier, and `task.yaml` is gone.
- `tests/workflow/test_declared.py`: pure-functions tier. It compared the declared model with the YAML being deleted.
- `tests/workflow/test_builtin_integrate.py`: pure-functions tier, by its own docstring, and it asserts on `builtin/integrate.yaml`. The plan's file list does not name it, but it cannot survive the deletion of `builtin/`. Before deleting it, the implementer must confirm that every assertion it makes about the declared `INTEGRATE` model is already covered in `tests/test_integrate_workflow.py`. Any assertion that is not covered is reported, not re-invented.

Modify by deleting the yaml half:

- `tests/test_engine.py` (engine tier: fake adapter, canned result files, crash-mid-phase resume): drop the `yaml` param and the `phases.from_loader` fixture (around lines 251-271). Only the pygents path remains. Point imports of moved names at their runtime location.
- `tests/workflow/test_phases.py` (pure-functions tier): delete the `from_loader` section (from line 252) and its `load_builtin`/`load_workflow`/registry imports. Keep the phase-model tests.
- Pure-functions tier, repoint imports only: `tests/test_prompt.py`, `tests/test_dispatch.py` and `tests/test_results.py`. Delete any case that exercises the loader `AgentPhase` or the by-name gate path. Repoint `EngineError` and `bind_arguments` imports to their runtime location. `tests/test_dispatch.py` also drops the `workflow` argument from every surviving `dispatch.evaluate_gates(...)` call and every `dispatch.AgentRunner(workflow=..., ...)` construction (its `_workflow(...)` and `_NoLookupWorkflow()` test helpers included), not only the calls in the deleted by-name-path cases -- `evaluate_gates` and `AgentRunner` no longer take that parameter at all.
- Engine tier (fake adapter or temporary board, no network): `tests/test_cli.py`, `tests/test_orchestrate.py`, `tests/test_integration.py`, `tests/test_integrate_workflow.py`, `tests/runtime/test_compile.py`, `tests/runtime/test_checkpoint.py` and `tests/steps/test_verify.py`. In each file:
  - Delete the tests for `interrupted_phase`, `resume_start_phase`, `_skipped_origin`, the `--engine` refusal and default, the `ENGINES` listing, and the yaml-only resumable-status case.
  - Collapse any `engine` parametrisation to pygents.
  - Repoint moved imports.
  - Drop the `workflow=` keyword from every fake `RunnerFactory`/`AgentRunner` these files construct, matching the `dispatch.py` parameter removal above.
  - `tests/test_cli.py` also deletes `test_a_workflow_that_will_not_load_reaches_the_operator_unchanged` (monkeypatches `cli.load_builtin`, which no longer exists as a `cli` attribute) and `test_a_run_recorded_with_an_unknown_workflow_is_an_envelope` (expects a `resume` on an unknown `run.workflow` to raise `WorkflowLoadError`; the surviving pygents resume path loads `WORKFLOW_NAME` unconditionally and never reads `run.workflow`, so this case can no longer happen), and drops its now-dead `WorkflowLoadError` import.
- `tests/e2e/test_production_wiring.py` and `tests/e2e/test_parallel_milestone.py`: engine tier, default suite, with a fake `claude` and a temporary board.
  - Collapse the `engine` parametrisation and stop passing `--engine`.
  - Delete the explicit assertion from Task 5.1 that the yaml engine escalates on the first critic block.
  - Rule 4 stays intact: the fake `claude` gains no knowledge.
- `tests/e2e/test_real_harness.py`: end-to-end tier, opt-in and skipped by default. Collapse its `engine` parametrisation to the pygents case and keep its guard and assertions.

Done when:

- `uv run pytest` is green, including `tests/e2e`.
- `grep -rn "workflow.loader\|workflow.registry\|load_builtin\|from agent_manager import engine\|agent_manager.engine\|from_loader\|import yaml\|WorkflowLoadError" src tests` returns nothing.
- `am run --help` shows no `--engine`.

Note: the exploration summary handed to this stage was cut off at 8000 characters, partway through the test-placement rule. The tiers above were read directly from the design spec §14 rather than taken from the truncated text.

---

# Make pygents the only engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Delete the YAML engine, its loader, registry, builtin documents, the three yaml resume helpers and `--engine`, and relocate the helpers the pygents walk still borrows from `engine.py` into a pygents-free `runtime/walk.py` (plus `EngineError` into `runtime/errors.py`), leaving every run and resume on `runtime.engine.run_subtask`.

**Architecture:** Removal runs outside-in so every commit keeps the whole suite green. Task 1 removes the engine *choice* (`--engine`, the yaml walk branches, the yaml resume) from `cli.py`, `orchestrate.py`, `integration.py` and their tests, while `engine.py`, the loader and the registry still exist. Task 2 removes the by-name dispatch path and the loaded `Workflow` the runner factory threads through, and rewrites every test that built a workflow from YAML into the declared phase model. Task 3, with nothing left calling the YAML engine, moves the shared helpers into `runtime/walk.py` and `EngineError` into `runtime/errors.py`, repoints every import, and deletes `engine.py`, the loader, the registry, `builtin/`, `from_loader`, their tests and `pyyaml`. Task 4 fixes the docstrings that still name deleted code and runs the done-when checks.

**Tech Stack:** Python 3.12, Typer, Pydantic, pygents 0.7.0 (runtime only), pytest with `--import-mode=importlib`, `uv`.

**Spec:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m6/task-make-pygents-the-only-7a744199/docs/superpowers/specs/task-make-pygents-the-only-7a744199-design.md` (reproduced verbatim above).

Every path below is relative to the worktree root `/home/paulomtts/Code/agent-manager/.claude/worktrees/m6/task-make-pygents-the-only-7a744199`. Run every command from there.

## Global Constraints

- Rule 1: only `src/agent_manager/runtime/` imports pygents; `workflow/phases.py` never does. `runtime/walk.py` and `runtime/errors.py` are inside `runtime/` but must not import pygents either, because `dispatch.py`, `prompt.py` and `results.py` import them.
- `runtime/__init__.py` stays exactly `"""The pygents engine: the only package allowed to import pygents."""` with no imports.
- Rule 2: every task ends with `uv run pytest` green, `tests/e2e` included.
- Rule 3: never break or return out of `agent.run()`; never checkpoint at `AFTER_TURN`; never register a pygents hook as a closure. Nothing in this plan touches `runtime/engine.py`'s loop, `runtime/checkpoint.py`'s hook or `runtime/compile.py`'s tools beyond import lines and comments.
- Rule 4: a fake `claude` in a test never knows more than its brief tells it. `tests/e2e/fake_claude.py` is not edited.
- Rule 5 / G10: `SubtaskSummary` fields, journal lines, phase and attempt rows, escalation payloads, `run.workflow` values and JSON envelopes do not change. `cli.WORKFLOW_NAME = "task"` and `orchestrate.MILESTONE_WORKFLOW = "milestone"` stay.
- No new test is written. Every test change is a deletion, an import repoint, a collapse of an `engine` parametrisation to its pygents case, or a fixture translation from a YAML document to the declared phase-model workflow `phases.from_loader` would have produced from it.
- `AgentRegistry._registry.pop` in `runtime/engine.py::_forget` stays as it is.
- `README.md` and the design-spec prose are card e46098be's; do not edit them.
- Verification command for this repo: `uv run pytest`.

## Review Focus

The spec forbids new tests, so none of these gets a new test. Each line names the existing test that pins it, or the verification command in Task 4 that checks it, or says plainly that nothing pins it.

1. An operator runs `am resume <run-id>` on a run recorded before checkpoints existed (a yaml-era run: subtask `started`, zero `checkpoints` rows). Expected: an `ok: false` `NotResumableError` envelope at exit 3 whose remedy names `agent-manager run --card`, never a traceback and never a remedy naming `--engine`. Pinned at the unit level by `tests/test_cli.py::test_checkpoint_resume_phase_refuses_a_card_with_no_checkpoint`; the command-level test for it (`test_a_yaml_run_has_no_checkpoint_and_a_pygents_resume_writes_nothing`) is deleted in Task 1 because it can only be set up with the yaml engine.
2. A script still passes `--engine yaml` or `--engine pygents` to `am run` or `am resume`. Expected: Typer's "No such option: --engine" usage error, exit 2, nothing written. Checked by the Task 4 Step 4 command; no test pins it.
3. Importing `agent_manager.dispatch`, `agent_manager.prompt`, `agent_manager.results`, `agent_manager.runtime.walk` or `agent_manager.runtime.errors` loads pygents. Expected: it does not (rule 1). Checked by the Task 4 Step 5 command; `tests/test_orchestrate.py::test_the_engine_selecting_modules_never_import_pygents` only guards `cli`, `orchestrate` and `integration`.
4. A milestone relaunch where a subtask was `stopped` by the previous run: its open checkpoint is now looked up unconditionally in `orchestrate.run_story_lane`. Expected: it continues from that checkpoint; a lookup failure escalates only that subtask. Pinned by `tests/test_orchestrate.py::test_a_pygents_relaunch_continues_a_matching_open_checkpoint_and_starts_the_rest_fresh` and `test_a_checkpoint_lookup_that_fails_escalates_that_subtask`.
5. `am resume` at the command level: its `ok: true` envelope, `--pretty`, exit 1 on an escalated resume, and `--verify` / `--allow-no-verification` reaching the gate context. Only the yaml-resume tests deleted in Task 1 pinned these. On pygents the checkpoint's pool carries the gate context the run *started* with, so a resume's own `--verify` never reaches it (the `resume` docstring already says so). Nothing pins this after Task 1; report it in the Task 4 hand-off so a follow-up card can decide whether to add pygents-shaped twins.

## Deviations from the spec, and why

Each of these is recorded here so a reviewer sees it once, with the reason; the tasks below implement them.

- `tests/workflow/test_declared.py` is trimmed, not deleted. Only four of its thirteen tests compare against the YAML (`test_task_equals_the_shipped_yaml`, `test_integrate_equals_the_shipped_yaml`, `test_task_callables_are_the_registry_bindings`, `test_integrate_callables_are_the_registry_bindings`). The other nine pin `TASK`/`INTEGRATE` data (timeouts, `validate()`, the critics' `on_fail` loops, no pygents import) and never touch YAML; deleting them would drop coverage the spec's own "pure removal" rule protects.
- `tests/test_dispatch.py` and `tests/test_prompt.py` translate their YAML-built phases into declared `phases.AgentPhase` instead of deleting every case that used the loader `AgentPhase`. About forty `AgentRunner` tests (retry, classification, briefs, journalling) built their phase from `AGENT_DOCUMENT`; deleting them all would gut the dispatch suite. The translation is the phase `from_loader` would have produced, and the assertions stay verbatim. The genuinely by-name-only cases are deleted: the six `evaluate_gates` tests that pass a name through `workflow.function` (each has a callable twin already in the file), `test_an_unregistered_result_model_is_a_named_engine_error` (by-name result lookup) and `test_a_phase_model_agent_phase_renders_exactly_like_the_yaml_one`.
- `tests/test_engine.py` gets the same translation: its YAML documents become declared workflows built over the same fake callables, and the builtin-task walks use `_fake_task`, which is `workflow.task.TASK` with each callable swapped for the fake of the same registry name. Without that there is no pygents half left to keep, because the pygents half converted the YAML through `from_loader`.
- `tests/test_cli.py` loses more than the spec's named tests: every test that drives the yaml resume (`_crash_mid_phase`, `_record_interrupted` and the twelve tests built on them) and the four `select_resumable` tests that pin the yaml "stopped is not resumable" wording. None can pass once stopped subtasks are resumable and every resume reads a checkpoint. Their pygents twins (`test_a_pygents_*`, `test_select_resumable_on_pygents_*`) stay.
- `cli.checkpoint_resume_phase`'s no-checkpoint message loses its "resume a yaml run without `--engine pygents`" remedy, because that flag no longer exists. The envelope shape is unchanged; only the remedy text changes.
- `cli.select_resumable` loses its relaunch-remedy code: with `stopped` counted as in flight, the "nothing in flight" branch can never see a stopped subtask, so that code is unreachable.
- `cli.resume_run` loses its `clock` parameter (it fed only the yaml walk) and `integration.WORKFLOW_NAME` goes (it fed only `load_builtin`).
- `dispatch.AgentRunner`'s by-name `result` lookup (`results.resolve_result_model` branch) and its `result_models` field stay. The spec does not list them, and `result_models` is a public field tests pin.

## File Structure

- Create `src/agent_manager/runtime/errors.py`: `EngineError`, nothing imported.
- Create `src/agent_manager/runtime/walk.py`: the helpers moved from `engine.py`, typed against `workflow.phases` only, no pygents.
- Delete `src/agent_manager/engine.py`, `src/agent_manager/workflow/loader.py`, `src/agent_manager/workflow/registry.py`, `src/agent_manager/workflow/builtin/task.yaml`, `src/agent_manager/workflow/builtin/integrate.yaml`.
- Modify `src/agent_manager/errors.py` (keeps `AgentPhaseFailed` only), `cli.py`, `orchestrate.py`, `integration.py`, `dispatch.py`, `prompt.py`, `results.py`, `runtime/engine.py`, `runtime/compile.py`, `runtime/checkpoint.py`, `runtime/context.py`, `workflow/__init__.py`, `workflow/phases.py`, `workflow/task.py`, `workflow/integrate.py`, `harness/registry.py`, `steps/verify.py`, `steps/rollup.py`, `steps/reducers.py`, `steps/plan_check.py`, `steps/docs_commit.py`, `pyproject.toml`, `uv.lock`.
- Delete tests `tests/workflow/test_loader.py`, `tests/workflow/test_registry.py`, `tests/workflow/test_builtin_task.py`, `tests/workflow/test_builtin_integrate.py`.
- Modify tests `tests/test_cli.py`, `tests/test_orchestrate.py`, `tests/test_integration.py`, `tests/test_integrate_workflow.py`, `tests/test_engine.py`, `tests/test_dispatch.py`, `tests/test_prompt.py`, `tests/test_results.py`, `tests/steps/test_verify.py`, `tests/runtime/test_compile.py`, `tests/runtime/test_checkpoint.py`, `tests/workflow/test_phases.py`, `tests/workflow/test_declared.py`, `tests/e2e/conftest.py`, `tests/e2e/test_production_wiring.py`, `tests/e2e/test_parallel_milestone.py`, `tests/e2e/test_milestone_run.py`, `tests/e2e/test_real_harness.py`.

---

### Task 1: Remove the engine choice

Deletes `--engine`, `Engine`/`ENGINES`, `_check_engine`, the yaml walk branches in `cli.drive_subtask`, `cli.resume_run` and `integration._resolve_conflict`, the three yaml resume helpers, the yaml resume itself, the `load_builtin` preflights in `run_card`, `_resume_from_checkpoint` and `run_milestone`, and every `engine` parameter in `cli`, `orchestrate` and `integration`. `drive_subtask` and `_resolve_conflict` still load the YAML document to hand to the runner factory; Task 2 removes that.

**Files:**
- Modify: `src/agent_manager/cli.py`
- Modify: `src/agent_manager/orchestrate.py`
- Modify: `src/agent_manager/integration.py`
- Test: `tests/test_cli.py`, `tests/test_orchestrate.py`, `tests/test_integration.py`, `tests/e2e/conftest.py`, `tests/e2e/test_production_wiring.py`, `tests/e2e/test_parallel_milestone.py`, `tests/e2e/test_milestone_run.py`, `tests/e2e/test_real_harness.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces (Task 2 relies on these exact signatures):
  - `cli.select_resumable(run: models.Run) -> tuple[models.StoryRun, models.SubtaskRun]`
  - `cli.drive_subtask(*, store, run_id, card, parent, subtask, repo_dir, commands=(), allow_no_verification=False, runner_factory=None, should_stop=None, resume_from=None) -> SubtaskDrive`
  - `cli.run_card(card_id, *, repo_dir, branch_prefix, base_branch="master", allow_no_verification=False, commands=(), runner_factory=None, clock=_utcnow) -> dict`
  - `cli.resume_run(run_id, *, repo_dir, allow_no_verification=False, commands=(), runner_factory=None) -> dict`
  - `orchestrate.run_milestone(milestone, *, repo_dir, base_branch, branch_prefix, commands=(), allow_no_verification=False, runner_factory=None, driver=None, clock=_utcnow, max_concurrent=1) -> dict`
  - `integration.integrate_milestone(stories, repo_dir, base_branch, branch_prefix, commands, allow_no_verification, store, run_id, runner_factory) -> IntegrateOutcome`
  - `integration._resolve_conflict(*, story_id, tip, files, branch, base_branch, worktree, repo_dir, commands, allow_no_verification, store, run_id, runner_factory) -> engine.SubtaskSummary`
  - Test helpers: `tests/test_cli.py::_record_walks(monkeypatch) -> list[tuple[Any, Any, dict]]`, `tests/test_integration.py::_stub_walk(monkeypatch) -> list[tuple[Any, Any, dict]]`, `tests/test_integration.py::_resolve(store, factory, tmp_path)`.

- [ ] **Step 1: Collapse `tests/test_cli.py` to the pygents walk**

Make these edits in `tests/test_cli.py`, in order.

a. Imports. Replace

```python
from agent_manager.errors import AgentPhaseFailed, EngineError
from agent_manager.steps.reducers import verification_gate
from agent_manager.workflow.loader import load_builtin
from agent_manager.workflow.registry import WorkflowLoadError

from agent_manager import engine as yaml_engine
from agent_manager.runtime import engine as runtime_engine
from agent_manager.workflow import loader
from agent_manager.workflow import task as task_workflow
```

with

```python
from agent_manager.engine import SubtaskSummary
from agent_manager.errors import AgentPhaseFailed, EngineError
from agent_manager.steps.reducers import verification_gate

from agent_manager.runtime import engine as runtime_engine
from agent_manager.workflow import loader
from agent_manager.workflow import task as task_workflow
```

b. Delete the function `_task_workflow`, the constant `SKIPPED_STRETCH` with its docstring, and these twelve tests (each from its `def` line to the line before the next top-level statement): `test_the_interrupted_phase_is_the_one_recorded_started`, `test_a_crash_between_phases_restarts_at_the_first_phase_not_done`, `test_a_skipped_stretch_restarts_at_the_phase_whose_when_decided_the_skip`, `test_a_run_that_lost_everything_after_the_first_phase_restarts_at_explore`, `test_a_run_that_only_lost_its_last_phase_restarts_there_and_not_at_plan_check`, `test_a_skipped_stretch_the_walk_ran_past_does_not_drag_the_restart_back`, `test_a_skipped_stretch_with_only_the_final_write_lost_restarts_at_mark_done`, `test_a_subtask_with_every_phase_done_has_no_phase_to_resume`, `test_an_interrupted_spec_backs_off_to_the_phase_whose_result_it_binds`, `test_an_interrupted_implement_backs_off_to_the_phase_that_binds_its_plan_hash`, `test_the_resume_walk_reads_its_producer_map_out_of_the_prompt_table`, `test_a_deterministic_phase_killed_mid_suite_restarts_at_itself_with_no_orphans`. Keep `AGENT_PHASE_NAMES`, `_recorded` and `test_orphan_attempts_are_exactly_the_ones_recorded_started`.

c. Replace the whole function `_record_walks` with

```python
def _record_walks(monkeypatch) -> list[tuple[Any, Any, dict[str, Any]]]:
    """Stub `runtime.engine.run_subtask`; every call is recorded.

    Patched on the module itself, so the stub is what `drive_subtask` reaches
    through its `runtime_engine` alias.
    """
    walks: list[tuple[Any, Any, dict[str, Any]]] = []

    def run_subtask(workflow, store, **kwargs):
        walks.append((workflow, store, kwargs))
        return SubtaskSummary(status="done")

    monkeypatch.setattr(runtime_engine, "run_subtask", run_subtask)
    return walks
```

d. Replace the whole parametrised test `test_drive_subtask_walks_the_engine_it_is_given_with_the_same_arguments` (its `@pytest.mark.parametrize("engine", ["yaml", "pygents"])` decorator included) with

```python
def test_drive_subtask_walks_task_with_the_same_arguments(monkeypatch):
    """Spec test 2: the walk is `runtime.engine.run_subtask` over `TASK`. The
    keywords are compared whole, so a `start_phase` or a `resume_from`
    sneaking into the call fails here."""
    walks = _record_walks(monkeypatch)
    seen: list[dict[str, Any]] = []
    factory, runner = _recording_factory(seen)
    store = object()
    subtask = _drive_row()

    def stop() -> bool:
        return False

    drive = cli.drive_subtask(
        store=store,
        run_id=DRIVE_RUN_ID,
        card=DRIVE_CARD,
        parent=DRIVE_PARENT,
        subtask=subtask,
        repo_dir=DRIVE_REPO,
        commands=["uv run pytest"],
        runner_factory=factory,
        should_stop=stop,
    )

    ((workflow, passed_store, kwargs),) = walks
    assert passed_store is store
    assert workflow is task_workflow.TASK
    assert kwargs == {
        "story_id": DRIVE_PARENT.id,
        "subtask": subtask,
        "repo_dir": DRIVE_REPO,
        "commands": ["uv run pytest"],
        "card": DRIVE_CARD,
        "parent_story": DRIVE_PARENT,
        "extra_context": cli.gate_context(["uv run pytest"], False),
        "agent_runner": runner,
        "should_stop": stop,
    }
    assert drive.summary.status == "done"
    assert drive.warnings == []
    (factory_call,) = seen
    assert isinstance(factory_call["workflow"], loader.Workflow)
    assert factory_call["workflow"].name == cli.WORKFLOW_NAME
    assert {key: value for key, value in factory_call.items() if key != "workflow"} == {
        "store": store,
        "run_id": DRIVE_RUN_ID,
        "story_id": DRIVE_PARENT.id,
        "card_id": DRIVE_CARD.id,
    }
```

e. Delete `test_drive_subtask_defaults_to_the_yaml_walk`, `test_drive_subtask_refuses_an_unknown_engine_before_building_a_runner` (with its parametrize decorator), `test_engines_lists_exactly_the_literal_values` and `test_run_card_hands_its_engine_to_drive_subtask` (with its `@requires_git`, `@requires_brd` and parametrize decorators). Also replace the section comment line `# ── engine selection (card 7fdec762) ─────────────────────────────────────────` with `# ── drive_subtask's walk (card 7fdec762) ─────────────────────────────────────`.

f. In `test_an_engine_error_escaping_the_walk_reaches_the_operator`, replace `monkeypatch.setattr(cli.engine, "run_subtask", exploding)` with `monkeypatch.setattr(cli.runtime_engine, "run_subtask", exploding)`.

g. Delete `test_a_workflow_that_will_not_load_reaches_the_operator_unchanged` with its two decorators.

h. In `test_a_milestone_run_calls_run_milestone_once_with_the_run_options`, replace

```python
                "max_concurrent": 4,
                "engine": "yaml",
```

with

```python
                "max_concurrent": 4,
```

i. Delete `test_a_bad_engine_is_a_usage_error_that_starts_nothing`, `test_the_engine_reaches_run_card`, `test_the_engine_reaches_run_milestone` and `test_a_dry_run_accepts_and_ignores_the_engine`, each with its parametrize decorators.

j. Delete the yaml resume: the helper `_crash_mid_phase`, the helper `_record_interrupted`, and these twelve tests with their decorators: `test_a_run_killed_mid_implement_resumes_and_leaves_no_started_attempt`, `test_the_resumed_dispatch_numbers_past_the_attempt_the_crash_left`, `test_resume_launches_no_harness`, `test_a_run_killed_in_spec_restarts_at_explore_so_specs_input_is_bound`, `test_resume_passes_its_own_allow_no_verification_into_the_gate_context`, `test_a_restart_at_plan_check_that_finds_no_plan_is_an_engine_error_not_a_traceback`, `test_resume_refuses_a_subtask_whose_every_phase_is_already_done`, `test_the_resume_command_prints_an_ok_envelope_and_exits_zero`, `test_resume_pretty_indents_the_same_envelope`, `test_a_resumed_walk_that_escalates_is_ok_true_and_exit_one`, `test_resume_passes_its_repeated_verify_options_into_the_gate_context`, `test_a_run_recorded_with_an_unknown_workflow_is_an_envelope`. Keep `CRASHED_AT`, `recording_runner` and `_resume_factory`.

k. Delete the four yaml-wording `select_resumable` tests: `test_select_resumable_tells_a_stopped_run_to_relaunch_the_milestone`, `test_select_resumable_names_only_the_stopped_cards_in_the_remedy`, `test_select_resumable_keeps_its_wording_when_nothing_is_stopped`, `test_select_resumable_does_not_count_a_stopped_subtask_as_in_flight`.

l. In `test_drive_subtask_hands_resume_from_to_the_pygents_walk`, replace

```python
        runner_factory=factory,
        engine="pygents",
        resume_from=checkpoint,
    )

    assert walks["yaml"] == []
    ((workflow, _store, kwargs),) = walks["pygents"]
```

with

```python
        runner_factory=factory,
        resume_from=checkpoint,
    )

    ((workflow, _store, kwargs),) = walks
```

m. Delete `test_drive_subtask_refuses_resume_from_on_yaml_before_building_a_runner`, `test_resume_run_refuses_an_unknown_engine_before_reading_anything`, `test_a_yaml_run_has_no_checkpoint_and_a_pygents_resume_writes_nothing` (with decorators), `test_resume_with_a_bad_engine_is_a_usage_error_that_starts_nothing` and `test_the_engine_reaches_resume_run` (each with its parametrize decorator).

n. Replace the head of `test_a_pygents_run_killed_in_plan_resumes_at_plan_from_its_checkpoint` —

```python
def test_a_pygents_run_killed_in_plan_resumes_at_plan_from_its_checkpoint(
    project, cards, monkeypatch
):
    """Spec test 3: the runner sees `plan` next, never `explore` or `spec`, and
    the yaml back-off helpers are never consulted."""
    run_id = _crash_pygents(project, cards, "plan")
    monkeypatch.setattr(cli, "interrupted_phase", _Forbidden("interrupted_phase"))
    monkeypatch.setattr(cli, "resume_start_phase", _Forbidden("resume_start_phase"))
    seen: list[str] = []
```

with

```python
def test_a_pygents_run_killed_in_plan_resumes_at_plan_from_its_checkpoint(project, cards):
    """Spec test 3: the runner sees `plan` next, never `explore` or `spec`."""
    run_id = _crash_pygents(project, cards, "plan")
    seen: list[str] = []
```

o. In `test_a_parked_milestone_subtask_resumes_on_pygents_instead_of_being_refused`, replace

```python
    """Spec test 4, and Review Focus 2: the run is a `milestone` run, which the
    yaml path cannot load and still refuses with its relaunch remedy."""
    run_id = _park_pygents(project, cards)

    with pytest.raises(cli.NotResumableError) as refused:
        cli.resume_run(run_id, repo_dir=project, runner_factory=_Forbidden("runner_factory"))
    assert "agent-manager run --milestone" in str(refused.value)

    after: list[str] = []
```

with

```python
    """Spec test 4: the run is a `milestone` run, and its parked subtask
    continues from its checkpoint instead of being refused."""
    run_id = _park_pygents(project, cards)

    after: list[str] = []
```

p. Remove every remaining `engine="pygents"` keyword argument. Use three replace-all edits on the file: `, engine="pygents"` → `` (covers `select_resumable(run, engine="pygents")` and the one-line `resume_run(...)` calls); the line `            engine="pygents",` (12 spaces, with its trailing newline) → `` ; the line `        engine="pygents",` (8 spaces, with its trailing newline) → ``. Then replace

```python
        cli.app, ["resume", run_id, "--repo-dir", str(project), "--engine", "pygents"]
```

with

```python
        cli.app, ["resume", run_id, "--repo-dir", str(project)]
```

q. Check nothing engine-shaped is left: `grep -n 'engine="\|"--engine"\|yaml_engine\|interrupted_phase\|resume_start_phase\|ENGINES\|WorkflowLoadError\|load_builtin' tests/test_cli.py` must print nothing.

- [ ] **Step 2: Collapse `tests/test_orchestrate.py`**

a. Delete the line `from agent_manager.workflow.registry import WorkflowLoadError`.

b. Replace-all the signature line `        engine="yaml",` (8 spaces, with its newline) with nothing: it occurs in `FakeDriver.__call__`, `IntegrateRecorder.__call__` and `GatedDriver.__call__`. Replace-all the line `                "engine": engine,` (16 spaces, with its newline) with nothing: it occurs in the `FakeDriver` and `IntegrateRecorder` call records.

c. In `test_subtasks_run_in_order_each_stacked_on_the_one_before`, replace

```python
        "runner_factory": factory,
        "engine": "yaml",
        "run_status": "started",
```

with

```python
        "runner_factory": factory,
        "run_status": "started",
```

d. Delete `test_run_milestone_hands_its_engine_to_every_driver_call_and_to_integrate` with its four decorators, `test_a_workflow_that_will_not_load_is_refused_before_anything_is_written` with its two decorators, and `test_a_yaml_relaunch_looks_up_no_checkpoint` with its two decorators.

e. Replace-all `, engine="pygents")` with `)` (two sites: the `_run(...)` calls in `test_a_pygents_relaunch_continues_a_matching_open_checkpoint_and_starts_the_rest_fresh` and `test_a_checkpoint_lookup_that_fails_escalates_that_subtask`).

- [ ] **Step 3: Collapse `tests/test_integration.py`**

a. Replace `from agent_manager import engine as yaml_engine` with `from agent_manager.engine import SubtaskSummary`.

b. In the module docstring replace

```
a real `Store` and journal, the shipped `builtin/integrate.yaml` driven by
`engine.run_subtask`, and `dispatch.AgentRunner` as the agent runner. The runner
```

with

```
a real `Store` and journal, `workflow.integrate.INTEGRATE` walked by
`runtime.engine.run_subtask`, and `dispatch.AgentRunner` as the agent runner. The runner
```

c. In `_integrate`, delete the parameter line `    engine: str = "yaml",` and, in its `integrate_milestone(...)` call, the line `        engine=engine,` that follows `        runner_factory=factory,`.

d. Delete `test_a_conflict_dispatches_exactly_once_for_the_conflicting_tip` (the yaml half; its pygents twin follows it).

e. In `test_a_conflict_resolves_the_same_way_on_the_pygents_engine`, replace `outcome = _integrate(repo, store, stories, factory=factory, engine="pygents")` with `outcome = _integrate(repo, store, stories, factory=factory)`, and replace the three comment lines

```python
    # Non-vacuity: only the pygents engine checkpoints, so a resolver walk with
    # no checkpoint means `integrate_milestone` never handed `engine` on and
    # this test passed on the yaml walk.
```

with

```python
    # Non-vacuity: the resolver walk went through the pygents engine, the only
    # walk that checkpoints.
```

f. Replace everything from `def _stub_both_walks(monkeypatch):` down to (not including) `def test_a_refusing_resolver_escalates_and_leaves_merge_head_in_place(` with

```python
def _stub_walk(monkeypatch):
    """Stub `runtime.engine.run_subtask`; every call is recorded."""
    walks: list[tuple[Any, Any, dict[str, Any]]] = []

    def run_subtask(workflow, store, **kwargs):
        walks.append((workflow, store, kwargs))
        return SubtaskSummary(status="done")

    monkeypatch.setattr(runtime_engine, "run_subtask", run_subtask)
    return walks


def _resolve(store, factory, tmp_path: Path):
    return integration._resolve_conflict(
        story_id=STORY_B,
        tip="m5/task-b",
        files=["shared.txt"],
        branch=INTEGRATION_BRANCH,
        base_branch=BASE,
        worktree=tmp_path / "integrate-worktree",
        repo_dir=tmp_path,
        commands=[PASS_CMD],
        allow_no_verification=False,
        store=store,
        run_id=RUN_ID,
        runner_factory=factory,
    )


def test_resolve_conflict_walks_integrate_with_the_same_arguments(
    monkeypatch, tmp_path: Path
) -> None:
    """Spec test 4: `INTEGRATE` on the pygents walk, with no `card`, no
    `parent_story` and no `resume_from`, and the factory gets the YAML document."""
    walks = _stub_walk(monkeypatch)
    factory_calls: list[dict[str, Any]] = []
    runner = object()

    def factory(**kwargs: Any) -> Any:
        factory_calls.append(kwargs)
        return runner

    store = _RecordingStore()

    summary = _resolve(store, factory, tmp_path)

    assert summary.status == "done"
    ((workflow, passed_store, kwargs),) = walks
    assert passed_store is store
    assert workflow is integrate_workflow.INTEGRATE
    expected_subtask = models.SubtaskRun(
        card_id=STORY_B,
        branch=INTEGRATION_BRANCH,
        base_branch=BASE,
        status="started",
        worktree_path=tmp_path / "integrate-worktree",
    )
    assert store.subtasks == [("integrate", expected_subtask)]
    assert kwargs == {
        "story_id": "integrate",
        "subtask": expected_subtask,
        "repo_dir": tmp_path,
        "commands": [PASS_CMD],
        "extra_context": {
            "merge_tip": "m5/task-b",
            "conflict_files": ["shared.txt"],
            **cli.gate_context([PASS_CMD], False),
        },
        "agent_runner": runner,
    }
    (factory_call,) = factory_calls
    assert isinstance(factory_call["workflow"], loader.Workflow)
    assert factory_call["workflow"].name == "integrate"
    assert {key: value for key, value in factory_call.items() if key != "workflow"} == {
        "store": store,
        "run_id": RUN_ID,
        "story_id": "integrate",
        "card_id": STORY_B,
    }


```

Keep `class _RecordingStore` where it is (it sits above `_stub_both_walks`).

- [ ] **Step 4: Collapse the default-suite and opt-in e2e modules**

a. `tests/e2e/conftest.py`:
  - Delete the fixture `engine` (the `@pytest.fixture(scope="module")` line through `return "yaml"`).
  - Replace the fixture `project` with

    ```python
    @pytest.fixture(scope="module")
    def project(tmp_path_factory, module_monkeypatch, toolchain) -> Path:
        """One directory that is both a real git repo on `main` and a real brd board."""
        base = tmp_path_factory.mktemp("e2e")
        module_monkeypatch.setenv("XDG_DATA_HOME", str(base / "xdg"))
        return _init_project(base / "project", "e2e-board")
    ```

  - In `completed_run`, change the signature to `def completed_run(project, cards, fake_claude_bin) -> dict[str, Any]:`, the docstring's first line to `"""One real `cli.run_card`, with NO `runner_factory`.`, and delete the argument line `        engine=engine,`.
  - In `run_milestone_cli`, change the signature to `def run_milestone_cli(fake_claude_bin) -> Callable[..., Any]:`, replace the docstring with

    ```python
        """`am run --milestone` through `CliRunner`, with no runner_factory anywhere.

        Depends on `fake_claude_bin` so the fake is first on `PATH`: the real
        `ClaudeAdapter` resolves `claude` to it through the real `run_direct`.
        """
    ```

    and delete the two argv lines `            "--engine",` and `            engine,`.
  - Delete `_engine_neutral`, the fixture `engine_parity` and the fixture `board_card_labels`.
  - Replace the `checkpoint_rows` docstring with

    ```python
        """How many `checkpoints` rows one run wrote.

        A run with none never reached the pygents walk's BEFORE_TURN hook, and
        every assertion about that run would have passed on something else.
        """
    ```

b. `tests/e2e/test_production_wiring.py`:
  - Delete the fixture `engine`.
  - Replace the whole test `test_the_selected_engine_is_the_one_that_walked` with

    ```python
    def test_the_run_went_through_the_pygents_walk(project, completed_run, checkpoint_rows):
        """Non-vacuity: the pygents walk is the only one that checkpoints."""
        rows = checkpoint_rows(project, completed_run["run_id"])
        assert rows > 0, "the run wrote no checkpoint: it never reached the pygents walk"
    ```

  - Delete `test_the_run_data_is_the_same_on_both_engines` and `test_under_yaml_a_critic_that_blocks_once_escalates_at_once`.
  - Change `def _run_one_card(root: Path, card: str, engine: str):` to `def _run_one_card(root: Path, card: str):` and delete its two argv lines `            "--engine",` and `            engine,`.
  - Replace-all `_run_one_card(root, card, "pygents")` with `_run_one_card(root, card)`.
  - Replace the head of `test_with_no_critic_block_no_brief_carries_a_feedback_section` so it reads

    ```python
    def test_with_no_critic_block_no_brief_carries_a_feedback_section(
        completed_run, agent_attempts
    ):
        """Spec "No block" / review focus 3: an empty `feedback` renders nothing,
        so a clean run's briefs are what they were before."""
    ```

    and change its last line `        assert FEEDBACK_SECTION not in text, (engine, name)` to `        assert FEEDBACK_SECTION not in text, name`.

c. `tests/e2e/test_parallel_milestone.py`:
  - Delete the fixture `engine`.
  - Change the signature of `test_two_lanes_overlap_in_implement_and_the_milestone_finishes` to `(parallel_board, rendezvous, run_milestone_cli, checkpoint_rows)` and replace its last lines

    ```python
        rows = checkpoint_rows(root, data["run_id"])
        assert (rows > 0) is (engine == "pygents"), (engine, rows)
        engine_parity(
            "parallel-two-lanes", engine, data, tmp=tmp_path, cards=board_card_labels(parallel_board)
        )
    ```

    with

    ```python
        rows = checkpoint_rows(root, data["run_id"])
        assert rows > 0, rows
    ```

  - Change the signature of `test_one_lane_runs_the_level_s_stories_one_after_the_other` to `(parallel_board, rendezvous, run_milestone_cli)` and delete its trailing `engine_parity(...)` call (three lines).

d. `tests/e2e/test_milestone_run.py`:
  - Delete the fixture `engine`.
  - Change the signature of `test_a_clean_three_story_milestone_runs_to_done_on_one_stacked_line` to `(milestone_board, run_milestone_cli, checkpoint_rows)` and replace its last lines

    ```python
        rows = checkpoint_rows(root, data["run_id"])
        assert (rows > 0) is (engine == "pygents"), (engine, rows)
        engine_parity(
            "milestone-clean", engine, data, tmp=tmp_path, cards=board_card_labels(milestone_board)
        )
    ```

    with

    ```python
        rows = checkpoint_rows(root, data["run_id"])
        assert rows > 0, rows
    ```

  - Change the signature of `test_a_review_failure_stops_the_milestone_and_a_relaunch_finishes_it` to `(milestone_board, review_fail_marker, run_milestone_cli, read_fake_log)`, delete its first body line `    labels = board_card_labels(milestone_board)`, delete its three `engine_parity(...)` lines, and replace the comment

    ```python
        # On pygents too, the relaunch re-drives b1 from its first phase: its
        # newest checkpoint is `escalated` with no turn left, which
        # `cli.continuable_checkpoint` never continues (card 02890d5d).
    ```

    with

    ```python
        # The relaunch re-drives b1 from its first phase: its newest checkpoint
        # is `escalated` with no turn left, which `cli.continuable_checkpoint`
        # never continues (card 02890d5d).
    ```

  - Delete the argv line pairs `                "--engine",` / `                "pygents",` (in the `run --card` invoke), `            "--engine",` / `            "pygents",` (in the `resume` invoke) and `            "--engine",` / `            "pygents",` (in `_pygents_milestone`).

e. `tests/e2e/test_real_harness.py`:
  - Replace the module-docstring paragraph

    ```
    The test runs once per engine (`[yaml]` and `[pygents]`, pygents-engine spec
    §9), so `-m e2e` alone pays for TWO real runs of this module. To pay for one,
    select an engine by id:
    `uv run pytest -m e2e -k pygents -v tests/e2e/test_real_harness.py`.
    ```

    with

    ```
    `-m e2e` pays for one real run of this module:
    `uv run pytest -m e2e -v tests/e2e/test_real_harness.py`.
    ```

  - Delete the fixture `engine`.
  - In `completed_run`, change the signature to `def completed_run(real_claude, project, cards) -> dict[str, Any]:`, its docstring's first line to `"""One real, paid `cli.run_card` -- no `runner_factory`, no`, and delete the argument line `        engine=engine,`.
  - Replace the whole test `test_the_selected_engine_is_the_one_that_walked` with

    ```python
    def test_the_run_went_through_the_pygents_walk(project, completed_run, checkpoint_rows):
        """Non-vacuity: the pygents walk is the only one that checkpoints (its
        BEFORE_TURN hook). Reuses the module's one paid run; costs nothing extra."""
        rows = checkpoint_rows(project, completed_run["run_id"])
        assert rows > 0, "the run wrote no checkpoint: it never reached the pygents walk"
    ```

f. Check: `grep -rn 'engine\b' tests/e2e/conftest.py tests/e2e/test_production_wiring.py tests/e2e/test_parallel_milestone.py tests/e2e/test_milestone_run.py tests/e2e/test_real_harness.py | grep -v 'the engine\|engine owes\|engine nor\|engine unwinds\|pygents engine'` must show no fixture, parameter or `--engine` argument.

- [ ] **Step 5: Run the changed tests to see them fail**

Run: `uv run pytest tests/test_cli.py tests/test_orchestrate.py tests/test_integration.py tests/e2e -q`
Expected: FAIL. Among the failures: `TypeError: FakeDriver.__call__() got an unexpected keyword argument 'engine'` (orchestrate still passes `engine=`), `NotResumableError` in `test_select_resumable_on_pygents_returns_a_lone_stopped_subtask` (yaml wording still the default), `AssertionError: the run wrote no checkpoint` in `test_the_run_went_through_the_pygents_walk` (the default walk is still yaml), and `test_resolve_conflict_walks_integrate_with_the_same_arguments` failing on `((workflow, passed_store, kwargs),) = walks` because the yaml walk ran instead of the stub.

- [ ] **Step 6: Remove the engine choice from `src/agent_manager/cli.py`**

a. Replace the import block

```python
from typing import Any, Literal, Protocol, cast, get_args

import typer

from agent_manager import (
    board,
    census,
    dag,
    dispatch,
    engine,
    models,
    prompt,
    store as store_module,
)
from agent_manager import engine as yaml_engine
from agent_manager.errors import EngineError
```

with

```python
from typing import Any, Protocol

import typer

from agent_manager import (
    board,
    census,
    dag,
    dispatch,
    engine,
    models,
    store as store_module,
)
from agent_manager.errors import EngineError
```

b. In `CheckpointMismatchError`'s docstring replace `"""`resume --engine pygents` found a checkpoint saved under another `TASK`.` with `"""`resume` found a checkpoint saved under another `TASK`.`.

c. Replace the whole function `select_resumable` with

```python
def select_resumable(run: models.Run) -> tuple[models.StoryRun, models.SubtaskRun]:
    """The one subtask of `run` that was in flight, or a refusal naming why not.

    Pure over the tree `load_run` assembled, like `find_subtask`: which subtask
    is resumable is a question about recorded state, and answering it before any
    store is opened is what keeps a refusal from minting a run directory.

    Exactly one `started` or `stopped` subtask is the resumable shape. A
    `stopped` subtask (addendum P4) was parked between phases, and its parked
    checkpoint is what `resume` continues from (card 02890d5d). Zero means the
    run finished, escalated or never started, and the statuses are listed
    because the fix differs for each; an escalation is `retry`'s, never this
    command's. More than one is a milestone-shaped run: this command drives one
    subtask the way `run --card` does, and choosing between them would leave the
    rest recorded in flight with nothing driving them.
    """
    resumable = ("started", "stopped")
    wanted = " or ".join(repr(status) for status in resumable)
    in_flight = [
        (story, subtask)
        for story in run.stories
        for subtask in story.subtasks
        if subtask.status in resumable
    ]
    if len(in_flight) == 1:
        return in_flight[0]
    if not in_flight:
        found = (
            ", ".join(
                f"{subtask.card_id}={subtask.status}"
                for story in run.stories
                for subtask in story.subtasks
            )
            or "no subtask at all"
        )
        raise NotResumableError(
            f"run {run.id!r} has no subtask recorded {wanted}, so there is no work"
            f" in flight to pick up (found: {found});"
            f" `agent-manager status {run.id}` shows the run as it stands"
        )
    cards = ", ".join(subtask.card_id for _story, subtask in in_flight)
    raise NotResumableError(
        f"run {run.id!r} has {len(in_flight)} subtasks recorded {wanted} ({cards}),"
        " and `resume` drives one subtask the way `run --card` does;"
        f" `agent-manager status {run.id}` shows all of them"
    )
```

d. Delete the three functions `_skipped_origin`, `interrupted_phase` and `resume_start_phase`.

e. In `checkpoint_resume_phase`, replace the docstring

```python
    """The phase `resume --engine pygents` continues `card_id` at, or a refusal.

    Pure over the row `Store.latest_checkpoint` returned, so every refusal is
    testable without a store, and `resume_run` calls it before its first
    write. In order: no row (a yaml run, or one that died before its first
    turn); a newest row `done` (only the final status write was lost); a
```

with

```python
    """The phase `resume` continues `card_id` at, or a refusal.

    Pure over the row `Store.latest_checkpoint` returned, so every refusal is
    testable without a store, and `resume_run` calls it before its first
    write. In order: no row (a run that died before its first turn, or one
    that predates checkpoints); a newest row `done` (only the final status
    write was lost); a
```

and replace the first refusal

```python
        raise NotResumableError(
            f"card {card_id} in run {run_id!r} has no checkpoint to resume from:"
            " the run was driven on the yaml engine, or it died before its first"
            " turn; resume a yaml run without `--engine pygents`"
        )
```

with

```python
        raise NotResumableError(
            f"card {card_id} in run {run_id!r} has no checkpoint to resume from:"
            " the run died before its first turn, or it predates checkpoints;"
            " start a fresh run with `agent-manager run --card`"
        )
```

f. In `continuable_checkpoint`'s docstring replace `refuses, it starts the card from its first phase as the yaml engine does` / `(card 02890d5d).` with `refuses, it starts the card from its first phase (card 02890d5d).`

g. Delete the `Engine = Literal["yaml", "pygents"]` assignment with its docstring and the `ENGINES: tuple[str, ...] = get_args(Engine)` assignment with its docstring.

h. Replace the whole function `drive_subtask` with

```python
def drive_subtask(
    *,
    store: Store,
    run_id: str,
    card: models.Card,
    parent: models.Card,
    subtask: models.SubtaskRun,
    repo_dir: Path,
    commands: Sequence[str] = (),
    allow_no_verification: bool = False,
    runner_factory: RunnerFactory | None = None,
    should_stop: Callable[[], bool] | None = None,
    resume_from: store_module.Checkpoint | None = None,
) -> SubtaskDrive:
    """Walk one subtask through `workflow.task.TASK` under a store the caller owns.

    Addendum O4's shared driver. `run_card` calls it once, and a milestone runner
    calls it once per subtask against one store and one run id. The caller owns
    everything around the walk: the board reads, the run id, opening and
    closing the store, and the run/story/subtask rows. This function catches
    nothing. An escalation is `summary.status == "escalated"`, not an exception.
    `should_stop` goes straight to the engine; a stop is
    `summary.status == "stopped"`.

    The walk is `runtime.engine.run_subtask` over `TASK`. `resume_from` (card
    02890d5d) continues it from a saved checkpoint; it joins the walk's
    keywords only when given, so a fresh walk is called exactly as before.
    """
    workflow = load_builtin(WORKFLOW_NAME)
    factory = default_runner_factory if runner_factory is None else runner_factory
    runner = factory(
        workflow=workflow,
        store=store,
        run_id=run_id,
        story_id=parent.id,
        card_id=card.id,
    )
    walk: dict[str, Any] = {
        "story_id": parent.id,
        "subtask": subtask,
        "repo_dir": repo_dir,
        "commands": commands,
        "card": card,
        "parent_story": parent,
        "extra_context": gate_context(commands, allow_no_verification),
        "agent_runner": runner,
        "should_stop": should_stop,
    }
    if resume_from is not None:
        walk["resume_from"] = resume_from
    summary = runtime_engine.run_subtask(task_workflow.TASK, store, **walk)
    # `AgentRunner` collects gate warnings out of band (dispatch.py:375):
    # its signature returns a result, so a warning has nowhere else to go,
    # and dropping them is the §12 failure this whole list exists to prevent.
    warnings = list(summary.warnings) + list(getattr(runner, "warnings", []))
    return SubtaskDrive(summary=summary, warnings=warnings)
```

i. In `run_card`: delete the parameter line `    engine: Engine = "yaml",`; replace the docstring's first line with `"""Drive one subtask card through `workflow.task.TASK` once, and report.`; delete the docstring paragraph

```
    `engine` goes to `drive_subtask` unchanged; the preflight loads the YAML
    document on both engines.
```

(and the blank line before it); delete the three lines

```python
    # Fail-fast preflight: a workflow that will not load must leave no run
    # directory, so it is checked before `Store.open`. `drive_subtask` loads its own.
    load_builtin(WORKFLOW_NAME)
```

(and the blank line after them); and delete the argument line `            engine=engine,` in its `drive_subtask(...)` call.

j. Delete the function `_check_engine`. In `_check_run_targets`, delete the parameter line `    engine: str = "yaml",`, change the docstring's first line to `"""Refuse a bad `--card` / `--milestone` / `--dry-run` / `--max-concurrent` combination as a usage error.`, delete the three docstring lines beginning ``    `--engine` is typed `str` because Typer 0.27.2 cannot take a `Literal` ``, and delete the final line `    _check_engine(engine)`.

k. In the `run` command, delete the whole `engine: str = typer.Option(...)` parameter (8 lines), replace

```python
    _check_run_targets(
        card=card,
        milestone=milestone,
        dry_run=dry_run,
        max_concurrent=max_concurrent,
        engine=engine,
    )
    selected = cast(Engine, engine)
    lanes = DEFAULT_MAX_CONCURRENT if max_concurrent is None else max_concurrent
```

with

```python
    _check_run_targets(
        card=card,
        milestone=milestone,
        dry_run=dry_run,
        max_concurrent=max_concurrent,
    )
    lanes = DEFAULT_MAX_CONCURRENT if max_concurrent is None else max_concurrent
```

and delete both argument lines `                engine=selected,` (one in the `orchestrate.run_milestone(...)` call, one in the `run_card(...)` call).

l. Replace `_resume_from_checkpoint`'s docstring with

```python
    """Continue the run's one in-flight subtask from its newest checkpoint.

    Every refusal that needs no store -- nothing in flight, a card the board
    lost -- comes before `Store.open`. The checkpoint can only be read through
    the store, so its refusals (`checkpoint_resume_phase`) come right after it
    is opened and before the first write. Then the orphan attempts are marked
    `harness_error`, the run, story and subtask are recorded `started`, and
    `drive_subtask` walks `TASK` from the checkpoint, whose queue says where the
    walk goes on. A milestone run records `workflow="milestone"`, which names no
    workflow; every subtask is walked through `TASK` either way (card 02890d5d).
    """
```

then replace `    story, subtask = select_resumable(run, engine="pygents")` with `    story, subtask = select_resumable(run)`, delete the line `    load_builtin(WORKFLOW_NAME)` that follows the two `board.show` calls, and delete the argument line `            engine="pygents",` in its `drive_subtask(...)` call.

m. Replace the whole function `resume_run` with

```python
def resume_run(
    run_id: str,
    *,
    repo_dir: Path,
    allow_no_verification: bool = False,
    commands: Sequence[str] = (),
    runner_factory: RunnerFactory | None = None,
) -> dict[str, Any]:
    """Pick one stopped or killed subtask back up from its checkpoint (§9, card 02890d5d).

    The order is load-bearing in the same way `run_card`'s is, only inverted:
    every refusal -- unknown run, nothing in flight, a card the board lost --
    happens before `Store.open`, because `Store.open` constructs a `Journal`
    and therefore mints a run directory, and a refusal that left one behind
    would be this command writing state for a run it declined to touch.

    Branch, base branch and worktree come from the recorded `SubtaskRun` and
    never from a flag: §9's "the run records what it was started with" is the
    reason the record exists. The two knobs the record does *not* carry --
    `models.RunConfig` has no suite commands and no `allow_no_verification` --
    are taken as arguments here rather than grown onto the model.
    """
    root = resolve_repo_dir(repo_dir)
    conn = store_module.open_db(root)
    try:
        run = store_module.load_run(conn, run_id)
        if run is None:
            raise UnknownRunError(
                f"run {run_id!r} is not in the projection for {root}"
                " (`agent-manager runs` lists the ones that are)"
            )
    finally:
        conn.close()
    return _resume_from_checkpoint(
        run,
        root=root,
        allow_no_verification=allow_no_verification,
        commands=commands,
        runner_factory=runner_factory,
    )
```

n. In the `resume` command, delete the whole `engine: str = typer.Option(...)` parameter (9 lines), replace the docstring with

```python
    """Continue a stopped or killed subtask from its checkpoint, and drive it to the end.

    No `--base-branch` and no `--branch-prefix`: both were decided when the run
    started and are recorded on the subtask (§9). `--allow-no-verification` and
    `--verify` are offered because `models.RunConfig` carries neither the opt-out
    nor the suite commands. The checkpoint carries the gate context the run
    started with.
    """
```

delete the line `    _check_engine(engine)`, and delete the argument line `            engine=cast(Engine, engine),`.

o. Check: `grep -n 'Engine\b\|ENGINES\|_check_engine\|yaml_engine\|engine=\|interrupted_phase\|resume_start_phase\|_skipped_origin\|cast(' src/agent_manager/cli.py` must print nothing.

- [ ] **Step 7: Remove the engine choice from `src/agent_manager/orchestrate.py`**

a. In the module docstring replace

```
The order is load-bearing. Everything that can refuse -- an unknown milestone,
a blocker cycle, a story with two in-milestone blockers, a workflow that will
not load -- runs before the first write, so a refusal leaves no run directory,
no store, no fetch and no prune behind.
```

with

```
The order is load-bearing. Everything that can refuse -- an unknown milestone,
a blocker cycle, a story with two in-milestone blockers -- runs before the
first write, so a refusal leaves no run directory, no store, no fetch and no
prune behind.
```

b. Delete the import line `from agent_manager.workflow.loader import load_builtin`.

c. In `Driver`: replace the docstring sentence `` `resume_from` (card 02890d5d) is passed only when a pygents relaunch found`` / `a checkpoint to continue, so a driver written before it keeps working.` with `` `resume_from` (card 02890d5d) is passed only when a relaunch found a`` / `checkpoint to continue, so a driver written before it keeps working.`, and delete the line `        engine: cli.Engine = "yaml",` (8 spaces) in `__call__`. Do this edit before item d.

d. Replace-all the line `    engine: cli.Engine = "yaml",` (4 spaces) with nothing: it is in `run_story_lane` and `run_milestone`.

e. In `run_story_lane`'s docstring replace

```
    On `engine="pygents"` each subtask's open checkpoint is looked up first
    (`cli.continuable_checkpoint`), inside the same `try`, and handed to the
    driver as `resume_from` when it can be continued.
```

with

```
    Each subtask's open checkpoint is looked up first
    (`cli.continuable_checkpoint`), inside the same `try`, and handed to the
    driver as `resume_from` when it can be continued.
```

and replace

```python
            # Relaunch continuation (card 02890d5d): on pygents a card whose
            # open checkpoint was saved under this `TASK` continues from it; a
            # changed workflow, a closed card or no row starts it fresh, with
            # no error. The keyword is passed only when there is a row, so a
            # driver that predates it keeps working. Yaml looks nothing up.
            extra: dict[str, Any] = {}
            if engine == "pygents":
                checkpoint = cli.continuable_checkpoint(store, subtask.id)
                if checkpoint is not None:
                    extra["resume_from"] = checkpoint
```

with

```python
            # Relaunch continuation (card 02890d5d): a card whose open
            # checkpoint was saved under this `TASK` continues from it; a
            # changed workflow, a closed card or no row starts it fresh, with
            # no error. The keyword is passed only when there is a row, so a
            # driver that predates it keeps working.
            extra: dict[str, Any] = {}
            checkpoint = cli.continuable_checkpoint(store, subtask.id)
            if checkpoint is not None:
                extra["resume_from"] = checkpoint
```

and delete the argument line `                engine=engine,` in its `drive(...)` call.

f. In `run_milestone`: delete the docstring paragraph

```
    `engine` goes unchanged to every driver call on every lane thread and to
    Integrate; the preflight loads the YAML document on both engines.
```

(and the blank line before it); delete the three lines

```python
    # Fail-fast preflight, as `run_card` does: a workflow that will not load
    # must leave no run directory. The driver loads its own copy.
    load_builtin(cli.WORKFLOW_NAME)
```

delete the argument line `                        engine=engine,` in the `pool.submit(...)` call, and delete the argument line `            engine=engine,` in the `integration.integrate_milestone(...)` call.

g. Check: `grep -n 'engine\b' src/agent_manager/orchestrate.py` shows only the docstring mentions of `engine._stop` and "the engine parks between phases".

- [ ] **Step 8: Remove the engine choice from `src/agent_manager/integration.py`**

a. In the module docstring replace

```
A conflicting tip is handed to the builtin `integrate.yaml` (resolve, then
verify) through `engine.run_subtask`, under a synthetic "Integrate" story with
```

with

```
A conflicting tip is handed to `workflow.integrate.INTEGRATE` (resolve, then
verify) through `runtime.engine.run_subtask`, under a synthetic "Integrate" story with
```

b. Delete the import line `from agent_manager import engine as yaml_engine`.

c. Replace the signature tail and docstring of `_resolve_conflict`

```python
    runner_factory: cli.RunnerFactory,
    engine: cli.Engine = "yaml",
) -> yaml_engine.SubtaskSummary:
    """Drive the `integrate` workflow once for one conflicting tip.

    The synthetic subtask is recorded before `run_subtask` journals its first
    phase. The caller has already recorded the synthetic story.

    `engine` picks the walk as `cli.drive_subtask` does (card 7fdec762): `yaml`
    walks the loaded `builtin/integrate.yaml`, `pygents` walks
    `workflow.integrate.INTEGRATE`, with the same keywords. The runner factory
    gets the loaded YAML document on both. The parameter shadows the
    module-level `engine` name here, so the walks are reached as `yaml_engine`
    and `runtime_engine`. Any other value is refused before anything is
    recorded.
    """
    if engine not in cli.ENGINES:
        raise ValueError(
            f"unknown engine {engine!r}; expected one of {', '.join(cli.ENGINES)}"
        )
    workflow = load_builtin(WORKFLOW_NAME)
```

with

```python
    runner_factory: cli.RunnerFactory,
) -> engine.SubtaskSummary:
    """Drive the `integrate` workflow once for one conflicting tip.

    The synthetic subtask is recorded before `run_subtask` journals its first
    phase. The caller has already recorded the synthetic story. The walk is
    `runtime.engine.run_subtask` over `workflow.integrate.INTEGRATE`.
    """
    workflow = load_builtin(WORKFLOW_NAME)
```

d. Replace

```python
    if engine == "pygents":
        return runtime_engine.run_subtask(integrate_workflow.INTEGRATE, store, **walk)
    return yaml_engine.run_subtask(workflow, store, **walk)
```

with

```python
    return runtime_engine.run_subtask(integrate_workflow.INTEGRATE, store, **walk)
```

e. In `integrate_milestone`, delete the parameter line `    engine: cli.Engine = "yaml",`, replace the docstring sentence `a tip conflicts. `engine` is handed to every resolver dispatch unchanged.` with `a tip conflicts.`, and delete the argument line `                engine=engine,` in its `_resolve_conflict(...)` call.

- [ ] **Step 9: Run the changed tests to see them pass**

Run: `uv run pytest tests/test_cli.py tests/test_orchestrate.py tests/test_integration.py tests/e2e -q`
Expected: PASS. If a test that used to run on the yaml default (for example a `run_card` test in `tests/test_cli.py` with no `engine=`) fails only now that it runs on pygents, stop and report it with the failure output: do not edit its assertions, because rule 5 says the two walks leave the same rows.

- [ ] **Step 10: Run the whole suite**

Run: `uv run pytest`
Expected: PASS, with the opt-in `e2e`-marked modules deselected.

- [ ] **Step 11: Commit**

```bash
git add src/agent_manager/cli.py src/agent_manager/orchestrate.py src/agent_manager/integration.py tests/test_cli.py tests/test_orchestrate.py tests/test_integration.py tests/e2e/conftest.py tests/e2e/test_production_wiring.py tests/e2e/test_parallel_milestone.py tests/e2e/test_milestone_run.py tests/e2e/test_real_harness.py
git commit -m "Remove the engine choice: every run and resume walks pygents"
```

---

### Task 2: Drop by-name dispatch and the workflow the runner factory threads

With the yaml walk gone from `src`, no agent phase that reaches `dispatch` carries its gates as names. This task deletes the by-name gate branch, `evaluate_gates`'s `workflow` parameter, `AgentRunner.workflow`, the `workflow` parameter of `cli.RunnerFactory` and `cli.default_runner_factory`, and the two `load_builtin` calls that fed it (`cli.drive_subtask`, `integration._resolve_conflict`). Every test that built a phase from YAML is rewritten over the declared phase model.

**Files:**
- Modify: `src/agent_manager/dispatch.py`
- Modify: `src/agent_manager/cli.py`
- Modify: `src/agent_manager/integration.py`
- Modify: `src/agent_manager/prompt.py`
- Test: `tests/test_dispatch.py`, `tests/test_engine.py`, `tests/test_prompt.py`, `tests/test_cli.py`, `tests/test_integration.py`, `tests/test_integrate_workflow.py`

**Interfaces:**
- Consumes (from Task 1): `cli.drive_subtask`, `integration._resolve_conflict`, `tests/test_cli.py::_record_walks`, `tests/test_integration.py::_stub_walk`/`_resolve` as listed in Task 1.
- Produces (Task 3 relies on these):
  - `dispatch.evaluate_gates(phase: phase_model.AgentPhase, values: Mapping[str, Any], warnings: list[str]) -> Verdict | None`
  - `dispatch.AgentRunner(store, launcher, run_id, story_id, card_id, adapters=..., result_models=..., harness_map=..., role_root=None, timeout=DEFAULT_TIMEOUT, clock=_utcnow, warnings=[])` — no `workflow` field.
  - `cli.RunnerFactory.__call__(self, *, store, run_id, story_id, card_id) -> engine.AgentPhaseRunner` and `cli.default_runner_factory(*, store, run_id, story_id, card_id)`.
  - Test helpers in `tests/test_engine.py`: `run_subtask` fixture (returns `runtime.engine.run_subtask`), `_workflow(document, functions)`, `_agent(name, role, *, inputs=(), result=None, writes=None)`, `_fake_task(functions) -> phase_model.Workflow`, and document factories `THREE_PHASES`, `ONE_PHASE`, `TWO_PHASES`, `GATED`, `SKIPPING`, `BEST_EFFORT`, `MIXED`, `DOCUMENT_PATHS`, `RESERVED_DETAILS`, `FOUR_PHASES`, `STOP_MIXED`, each `(functions: dict[str, Any]) -> phase_model.Workflow`.
  - Test helpers in `tests/test_dispatch.py`: `AGENT_DOCUMENT(functions)`, `SPEC_DOCUMENT(functions)`, `_agentic(*gates, **overrides) -> phases.Workflow`, `_workflow(document, functions)`, `_runner(store, launcher, tmp_path, worktree, **overrides)`, `_spec_runner(store, launcher, tmp_path, worktree)`.

- [ ] **Step 1: Rewrite `tests/test_dispatch.py` over the declared phase model**

a. Delete the two import lines `from agent_manager.workflow.loader import load_workflow` and `from agent_manager.workflow.registry import FunctionRegistry`.

b. Replace

```python
AGENT_DOCUMENT = """
name: agentic
phases:
  - name: explore
    kind: agent
    role: explorer
    result: FakeResult
    gates: [output_gate]
    retry: { max_attempts: 2, on: [schema_invalid, gate_failed] }
"""


def _workflow(document: str, functions: dict[str, object]):
    registry = FunctionRegistry()
    for name, fn in functions.items():
        registry.register(name, fn)
    return load_workflow(document, registry)
```

with

```python
def AGENT_DOCUMENT(functions: dict[str, object]) -> phases.Workflow:
    """One `explore` phase: role `explorer`, result `FakeResult`, gated by
    `functions["output_gate"]`, retried twice on `schema_invalid` and
    `gate_failed` -- the declared twin of the YAML document it replaced."""
    return _agentic(functions["output_gate"])


def _agentic(*gates, **overrides) -> phases.Workflow:
    """A one-phase workflow around `_model_phase`, carrying AGENT_DOCUMENT's
    retry policy unless `overrides` replaces it."""
    fields = {"retry": phases.Retry(2, ("schema_invalid", "gate_failed")), **overrides}
    return phases.Workflow("agentic", (_model_phase(*gates, **fields),))


def _workflow(document, functions: dict[str, object]) -> phases.Workflow:
    return document(functions)
```

c. Delete the six by-name `evaluate_gates` tests (their callable twins further down stay): `test_a_passing_gate_returns_no_verdict`, `test_a_failing_gate_returns_a_retryable_gate_failed_verdict`, `test_a_warning_gate_is_recorded_and_does_not_fail_the_attempt`, `test_a_gate_that_raises_is_a_fatal_gate_failure`, `test_a_gate_returning_a_non_mapping_is_a_fatal_gate_failure`, `test_a_gate_whose_parameter_nothing_supplies_is_a_named_engine_error`. Keep `test_the_result_is_bound_under_both_result_and_the_phase_name` and `test_a_reserved_key_is_not_overwritten_by_a_same_named_phase`.

d. Delete the class `_NoLookupWorkflow`. Replace the whole test `test_callable_gate_is_called_directly` with

```python
def test_callable_gate_is_called_directly():
    verdict = dispatch.evaluate_gates(
        _model_phase(lambda result: {"blocked": "x", "detail": "d"}),
        dispatch.gate_values({}, "explore", {"summary": "ok"}),
        [],
    )

    assert verdict.status == "gate_failed"
    assert verdict.fatal is False
    assert verdict.detail == "phase 'explore' gate '<lambda>' failed: blocked=x, detail=d"
```

Then delete the line `            _NoLookupWorkflow(),` (12 spaces, in `test_a_callable_gate_with_an_unsupplied_parameter_is_a_named_engine_error`) and replace-all the line `        _NoLookupWorkflow(),` (8 spaces) with nothing.

e. Take `workflow` out of the runner helpers. Replace

```python
def _runner(store, workflow, launcher, tmp_path, worktree, **overrides):
```

with

```python
def _runner(store, launcher, tmp_path, worktree, **overrides):
```

delete the line `        "workflow": workflow,` in its `kwargs` dict, replace

```python
def _spec_runner(store, workflow, launcher, tmp_path, worktree):
    return _runner(
        store,
        workflow,
        launcher,
```

with

```python
def _spec_runner(store, launcher, tmp_path, worktree):
    return _runner(
        store,
        launcher,
```

then replace-all `store, workflow, ` with `store, ` (every `_runner(store, workflow, ...)` and `_spec_runner(store, workflow, ...)` call, one- or multi-line), replace `_runner(opened, workflow, launcher, tmp_path, worktree)` with `_runner(opened, launcher, tmp_path, worktree)`, and in `test_a_result_class_wins_over_a_same_named_table_entry` replace

```python
    runner, _ = _runner(
        store,
        workflow,
        launcher,
```

with

```python
    runner, _ = _runner(
        store,
        launcher,
```

f. Translate the inline documents. In `test_a_gate_failure_outside_retry_on_is_not_retried` replace

```python
    document = AGENT_DOCUMENT.replace(
        "on: [schema_invalid, gate_failed]", "on: [schema_invalid]"
    )
```

with

```python
    def document(functions):
        return _agentic(functions["output_gate"], retry=phases.Retry(2, ("schema_invalid",)))
```

In `test_a_phase_with_no_retry_block_dispatches_exactly_once` replace

```python
    document = """
name: agentic
phases:
  - name: explore
    kind: agent
    role: explorer
    result: FakeResult
"""
```

with

```python
    def document(functions):
        return _agentic(retry=None)
```

Replace-all (two sites: `test_a_phase_with_no_result_model_declared_needs_no_table_entry` and `test_a_phase_with_no_declared_result_gets_no_result_contract`)

```python
    document = """
name: agentic
phases:
  - name: spec
    kind: agent
    role: explorer
    writes: docs/superpowers/specs/{stem}.md
"""
```

with

```python
    def document(functions):
        return _agentic(
            name="spec", result=None, retry=None, writes="docs/superpowers/specs/{stem}.md"
        )
```

Replace

```python
SPEC_DOCUMENT = """
name: agentic
phases:
  - name: spec
    kind: agent
    role: explorer
    result: SpecResult
    writes: docs/superpowers/specs/{stem}.md
"""
```

with

```python
def SPEC_DOCUMENT(functions: dict[str, object]) -> phases.Workflow:
    """One `spec` phase: role `explorer`, result `SpecResult`, writing the spec."""
    return _agentic(
        name="spec",
        result=results.SpecResult,
        retry=None,
        writes="docs/superpowers/specs/{stem}.md",
    )
```

In `test_all_four_outcome_names_are_journalled_as_distinct_values` replace

```python
    document = """
name: four
phases:
  - name: explore
    kind: agent
    role: explorer
    result: FakeResult
    gates: [output_gate]
"""
```

with

```python
    def document(functions):
        return _agentic(functions["output_gate"], retry=None)
```

Replace-all (two sites: `test_a_third_attempt_accumulates_both_feedback_blocks_with_one_contract` and `test_accumulated_feedback_blocks_keep_the_order_they_were_produced`)

```python
    document = AGENT_DOCUMENT.replace("max_attempts: 2", "max_attempts: 3")
```

with

```python
    def document(functions):
        return _agentic(
            functions["output_gate"], retry=phases.Retry(3, ("schema_invalid", "gate_failed"))
        )
```

g. Delete `test_an_unregistered_result_model_is_a_named_engine_error` (it pins the by-name result lookup a declared phase never takes).

h. Replace-all the line `        workflow=_workflow(AGENT_DOCUMENT, {"output_gate": lambda result: None}),` (8 spaces, with its newline) with nothing; it is in `test_a_default_runner_carries_the_shipped_result_model_table` and `test_the_production_runner_factory_carries_the_shipped_table`.

i. The four phase-model runner tests no longer read `workflow`. Replace

```python
def test_result_class_is_used_directly(store, tmp_path, worktree):
    workflow = _workflow(AGENT_DOCUMENT, {"output_gate": lambda result: None})
```

with

```python
def test_result_class_is_used_directly(store, tmp_path, worktree):
```

and the same pair for `test_a_result_class_wins_over_a_same_named_table_entry`, `test_phase_model_retry_is_honoured` and `test_phase_model_retry_does_not_retry_an_outcome_outside_on` (each `def` line followed by the same `    workflow = _workflow(AGENT_DOCUMENT, {"output_gate": lambda result: None})` line; delete that line only).

j. Check: `grep -n 'workflow,\|"workflow"\|workflow=\|load_workflow\|FunctionRegistry\|_NoLookupWorkflow' tests/test_dispatch.py` must print nothing.

- [ ] **Step 2: Rewrite `tests/test_prompt.py`'s phase fixture**

a. Delete the import line `from agent_manager.workflow.loader import AgentPhase`.

b. Replace

```python
def _phase(inputs, *, name="implement", role="coder", **extra) -> AgentPhase:
    return AgentPhase(kind="agent", name=name, role=role, inputs=list(inputs), **extra)
```

with

```python
def _phase(inputs, *, name="implement", role="coder", **extra) -> phases.AgentPhase:
    return phases.AgentPhase(name=name, role=role, inputs=tuple(inputs), result=None, **extra)
```

c. Delete `test_a_phase_model_agent_phase_renders_exactly_like_the_yaml_one` (with `_phase` now building the same type, it compares a value with itself).

- [ ] **Step 3: Rewrite `tests/test_engine.py` over the declared phase model**

a. Replace the module docstring and imports (from line 1 through `from agent_manager.workflow.registry import BUILTIN_FUNCTION_NAMES, FunctionRegistry`) with

```python
"""Behaviour of the subtask walk and deterministic phase execution (spec §6, §9, §12).

Engine tier per design §14 lines 477-492: canned fake callables in declared
phase-model workflows stand in for §14's fake adapter with canned result
files, and the store is a real temp SQLite projection plus a real temp JSONL
journal. No git, no `brd`, no harness process. Every walk is
`runtime.engine.run_subtask`, and each workflow below is the declared twin of
the YAML document the walk was first specified against, phase for phase.
"""

import dataclasses
import json
import typing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from agent_manager import dispatch, engine, models, results, store as store_module
from agent_manager.errors import AgentPhaseFailed
from agent_manager.harness.base import Outcome
from agent_manager.runtime import bridge
from agent_manager.runtime import engine as new_engine
from agent_manager.steps import (
    docs_commit,
    integrate,
    plan_check,
    reducers,
    rollup,
    verify,
    worktree,
)
from agent_manager.workflow import phases as phase_model
from agent_manager.workflow import task as task_workflow
```

b. Replace everything from the comment line `# `should_stop` tests whose yaml-engine behavior the pygents engine does not` down to and including the function `_workflow` (its `return load_workflow(document, _registry(functions))` line) with

```python
@pytest.fixture
def run_subtask():
    """`runtime.engine.run_subtask`, the one walk (pygents-engine design G7)."""
    return new_engine.run_subtask


Step = phase_model.Step


def _agent(name, role, *, inputs=(), result=None, writes=None) -> phase_model.AgentPhase:
    """An agent phase as `phases.from_loader` built one from a YAML `kind: agent`
    entry: no gates, no retry, the thirty-minute default timeout."""
    return phase_model.AgentPhase(
        name, role=role, inputs=tuple(inputs), result=result, writes=writes
    )


def _workflow(document, functions: dict[str, Any]) -> phase_model.Workflow:
    """`document` built over `functions`, keyed by the names its steps call."""
    return document(functions)
```

c. Replace the `THREE_PHASES` string constant with

```python
def THREE_PHASES(fn: dict[str, Any]) -> phase_model.Workflow:
    return phase_model.Workflow("three", (
        Step("alpha", fn["step.alpha"]),
        Step("beta", fn["step.beta"]),
        Step("gamma", fn["step.gamma"]),
    ))


def ONE_PHASE(fn: dict[str, Any]) -> phase_model.Workflow:
    return phase_model.Workflow("one", (Step("alpha", fn["step.alpha"]),))


def TWO_PHASES(fn: dict[str, Any]) -> phase_model.Workflow:
    return phase_model.Workflow("two", (
        Step("alpha", fn["step.alpha"]),
        Step("beta", fn["step.beta"]),
    ))
```

d. Replace-all the seven identical inline one-phase documents

```python
    document = """
name: one
phases:
  - name: alpha
    kind: deterministic
    run: step.alpha
"""
```

with `    document = ONE_PHASE`, and replace-all the three identical inline two-phase documents

```python
    document = """
name: two
phases:
  - name: alpha
    kind: deterministic
    run: step.alpha
  - name: beta
    kind: deterministic
    run: step.beta
"""
```

with `    document = TWO_PHASES`.

e. Replace the remaining inline documents one by one:

In `test_declared_args_reach_the_step_as_a_keyword`:

```python
    def document(fn):
        return phase_model.Workflow("board", (
            Step("mark_in_progress", fn["rollup.set_status"], args={"status": "in_progress"}),
        ))
```

In `test_a_step_is_called_with_only_the_parameters_it_declares`:

```python
    def document(fn):
        return phase_model.Workflow("one", (Step("worktree", fn["worktree.ensure"]),))
```

In `test_a_phase_named_like_a_context_key_runs_but_never_clobbers_it`:

```python
    def document(fn):
        return phase_model.Workflow("collide", (
            Step("worktree", fn["worktree.make"]),
            Step("after", fn["step.after"]),
        ))
```

In `test_a_warning_before_a_failing_gate_survives_into_the_summary`:

```python
    def document(fn):
        return phase_model.Workflow("gated", (
            Step("alpha", fn["step.alpha"], gates=(fn["first_gate"], fn["second_gate"])),
            Step("beta", fn["step.beta"]),
        ))
```

In `test_a_gate_on_a_phase_named_like_a_context_key_still_sees_the_real_value`:

```python
    def document(fn):
        return phase_model.Workflow("collide", (
            Step(
                "worktree",
                fn["worktree.ensure"],
                gates=(fn["worktree_gate"],),
                when=fn["when_worktree"],
                skip_to="after",
            ),
            Step("after", fn["step.after"]),
        ))
```

In `test_a_document_path_input_with_no_writing_phase_is_a_named_error`:

```python
    def document(fn):
        return phase_model.Workflow("orphan", (
            _agent("implement", "coder", inputs=("plan_path",)),
        ))
```

In `test_an_unresolvable_input_raises_out_of_the_walk_before_the_runner`:

```python
    def document(fn):
        return phase_model.Workflow("early", (
            _agent("spec", "spec_author", inputs=("explore",)),
        ))
```

In `test_a_phase_named_spec_path_never_clobbers_the_document_path`:

```python
    def document(fn):
        return phase_model.Workflow("reserved", (
            _agent("spec", "spec_author", writes="docs/superpowers/specs/{stem}.md"),
            Step("spec_path", fn["step.collide"]),
            _agent("implement", "coder", inputs=("spec_path",)),
            Step("after", fn["step.after"]),
        ))
```

f. Replace the module-level document constants:

```python
def GATED(fn: dict[str, Any]) -> phase_model.Workflow:
    return phase_model.Workflow("gated", (
        Step("alpha", fn["step.alpha"], gates=(fn["alpha_gate"],)),
        Step("beta", fn["step.beta"]),
    ))
```

```python
def SKIPPING(fn: dict[str, Any]) -> phase_model.Workflow:
    return phase_model.Workflow("skipping", (
        Step("plan_check", fn["plan_check.find"], when=fn["plan_check.has"], skip_to="implement"),
        Step("spec", fn["step.spec"]),
        Step("plan", fn["step.plan"]),
        Step("implement", fn["step.implement"]),
    ))
```

```python
def BEST_EFFORT(fn: dict[str, Any]) -> phase_model.Workflow:
    return phase_model.Workflow("board", (
        Step(
            "mark_in_progress",
            fn["rollup.set_status"],
            args={"status": "in_progress"},
            best_effort=True,
        ),
        Step("work", fn["step.work"]),
        Step(
            "mark_done",
            fn["rollup.done"],
            args={"status": "done"},
            gates=(fn["done_gate"],),
            best_effort=True,
        ),
    ))
```

```python
def MIXED(fn: dict[str, Any]) -> phase_model.Workflow:
    return phase_model.Workflow("mixed", (
        _agent("explore", "explorer", result=results.ExploreResult),
        Step("work", fn["step.work"]),
    ))
```

```python
def DOCUMENT_PATHS(fn: dict[str, Any]) -> phase_model.Workflow:
    return phase_model.Workflow("paths", (
        _agent("spec", "spec_author", writes="docs/superpowers/specs/{stem}.md"),
        _agent("plan", "planner", writes="docs/superpowers/plans/{stem}.md"),
        _agent("implement", "coder", inputs=("spec_path", "plan_path")),
        Step("after", fn["step.after"]),
    ))
```

```python
def RESERVED_DETAILS(fn: dict[str, Any]) -> phase_model.Workflow:
    return phase_model.Workflow("reserved-details", (
        Step("card_details", fn["step.collide"]),
        Step("parent_story_details", fn["step.collide"]),
        _agent("explore", "explorer", inputs=("card", "parent_story")),
    ))
```

```python
def FOUR_PHASES(fn: dict[str, Any]) -> phase_model.Workflow:
    return phase_model.Workflow("four", (
        Step("alpha", fn["step.alpha"]),
        Step("beta", fn["step.beta"]),
        Step("gamma", fn["step.gamma"]),
        Step("delta", fn["step.delta"]),
    ))


def STOP_MIXED(fn: dict[str, Any]) -> phase_model.Workflow:
    return phase_model.Workflow("stop_mixed", (
        Step("prepare", fn["step.prepare"]),
        _agent("explore", "explorer", result=results.ExploreResult),
        Step("finish", fn["step.finish"]),
    ))
```

g. Delete the yaml-only tests: `test_starting_at_a_named_phase_runs_only_from_there`, `test_an_unknown_starting_phase_is_an_error_before_anything_is_recorded`, `test_a_resume_started_at_explore_never_re_runs_the_worktree_phase`, `test_an_exception_from_should_stop_propagates_and_records_nothing`, `test_a_stopped_subtask_can_be_driven_again_to_done`, `test_run_one_step_resolves_a_loader_phases_names_through_the_workflow`, `test_run_one_step_fails_a_named_function_it_has_no_workflow_to_resolve`, the helper `_step_functions`, and the parity section (the comment `# ── parity: the shipped `task` workflow on both engines (spec Tests 3) ──────` and `test_new_engine_returns_same_summary_for_the_shipped_task`).

h. The builtin-task walks. Insert directly above `def _builtin_functions(`:

```python
_TASK_FUNCTION_NAMES: dict[Any, str] = {
    worktree.ensure: "worktree.ensure",
    rollup.set_status: "rollup.set_status",
    plan_check.find_validated_plan: "plan_check.find_validated_plan",
    plan_check.has_validated_plan: "plan_check.has_validated_plan",
    plan_check.mark_validated: "plan_check.mark_validated",
    docs_commit.commit_documents: "docs_commit.commit_documents",
    verify.run_suite: "verify.run_suite",
    reducers.verification_passed_gate: "verification_passed_gate",
    reducers.exploration_output_gate: "exploration_output_gate",
    reducers.verification_gate: "verification_gate",
    reducers.critic_blockers_gate: "critic_blockers_gate",
    reducers.implement_blocked_gate: "implement_blocked_gate",
    reducers.review_blockers_gate: "review_blockers_gate",
    reducers.review_gate: "review_gate",
    reducers.plan_hash_gate_adapter: "plan_hash_gate",
}
"""Every callable `TASK` holds, under the name the fake tables below use for it."""


def _fake_task(functions: dict[str, Any]) -> phase_model.Workflow:
    """`workflow.task.TASK` with every callable swapped for the fake of the same name.

    Phase order, inputs, `writes`, `skip_to` and the critics' `on_fail` are
    TASK's own; only what runs is fake. A callable `TASK` holds that `functions`
    does not name raises `KeyError` here, before anything is walked.
    """

    def fake(fn: Any) -> Any:
        return functions[_TASK_FUNCTION_NAMES[fn]]

    swapped: list[phase_model.Step | phase_model.AgentPhase] = []
    for phase in task_workflow.TASK.phases:
        if isinstance(phase, phase_model.Step):
            swapped.append(
                dataclasses.replace(
                    phase,
                    run=fake(phase.run),
                    gates=tuple(fake(gate) for gate in phase.gates),
                    when=None if phase.when is None else fake(phase.when),
                )
            )
        else:
            swapped.append(
                dataclasses.replace(phase, gates=tuple(fake(gate) for gate in phase.gates))
            )
    return phase_model.Workflow(task_workflow.TASK.name, tuple(swapped))
```

Then replace-all `workflow = load_builtin("task", _registry(functions))` with `workflow = _fake_task(functions)` (three sites), replace `workflow = load_builtin("task", _registry(_builtin_functions(calls, validated=validated)))` in `_walk_builtin` with `workflow = _fake_task(_builtin_functions(calls, validated=validated))`, delete the line `    assert sorted(functions) == sorted(BUILTIN_FUNCTION_NAMES)` in `test_the_builtin_task_document_walks_against_a_fake_registry`, and in that same test change `def agent_runner(phase: AgentPhase, context: dict[str, Any], rendered) -> dict[str, Any]:` to `def agent_runner(phase: phase_model.AgentPhase, context: dict[str, Any], rendered) -> dict[str, Any]:`.

i. Replace-all the line `        workflow=workflow,` (8 spaces, with its newline) with nothing; it is in the two `dispatch.AgentRunner(...)` calls of the decision-O7 tests.

j. The three `extra_context` tests. In `test_extra_context_reaches_a_deterministic_phase_binding` replace

```python
    registry = FunctionRegistry()
    registry.register("only.step", step)
    document = tmp_path / "one.yaml"
    document.write_text(
        "name: one\n"
        "description: one deterministic phase\n"
        "phases:\n"
        "  - name: only\n"
        "    kind: deterministic\n"
        "    run: only.step\n",
        encoding="utf-8",
    )
    workflow = load_workflow(document, registry)
```

with

```python
    workflow = phase_model.Workflow("one", (Step("only", step),))
```

In `test_extra_context_may_not_redefine_a_reserved_key` and `test_extra_context_may_not_redefine_the_base_branch_alias` replace-all

```python
    registry = FunctionRegistry()
    registry.register("only.step", lambda: {"ok": True})
    document = tmp_path / "one.yaml"
    document.write_text(
        "name: one\n"
        "description: one deterministic phase\n"
        "phases:\n"
        "  - name: only\n"
        "    kind: deterministic\n"
        "    run: only.step\n",
        encoding="utf-8",
    )
    workflow = load_workflow(document, registry)
```

with

```python
    workflow = phase_model.Workflow("one", (Step("only", lambda: {"ok": True}),))
```

k. In `test_an_error_no_phase_handles_escalates_at_the_phase_that_was_running` replace `        phase_model.from_loader(workflow),` with `        workflow,` and its docstring's first sentence `"""Review Focus 5. Not parametrised: the yaml engine has no bridge. An` with `"""Review Focus 5. An`.

l. Check: `grep -n 'load_workflow\|load_builtin\|FunctionRegistry\|_registry(\|from_loader\|start_phase\|kind: deterministic\|kind: agent\|BUILTIN_FUNCTION_NAMES' tests/test_engine.py` must print nothing.

- [ ] **Step 4: Drop `workflow` from the fake factories in `tests/test_cli.py`, `tests/test_integration.py` and `tests/test_integrate_workflow.py`**

a. `tests/test_cli.py`: in `_resume_factory` change `    def factory(*, workflow, store, run_id, story_id, card_id):` to `    def factory(*, store, run_id, story_id, card_id):`; delete the import line `from agent_manager.workflow import loader`; in `test_drive_subtask_walks_task_with_the_same_arguments` replace

```python
    (factory_call,) = seen
    assert isinstance(factory_call["workflow"], loader.Workflow)
    assert factory_call["workflow"].name == cli.WORKFLOW_NAME
    assert {key: value for key, value in factory_call.items() if key != "workflow"} == {
```

with

```python
    (factory_call,) = seen
    assert factory_call == {
```

b. `tests/test_integration.py`: delete the import line `from agent_manager.workflow import loader`; replace `FakeFactory.__call__` with

```python
    def __call__(self, *, store, run_id, story_id, card_id):
        self.calls.append({"run_id": run_id, "story_id": story_id, "card_id": card_id})
        adapter = _FakeAdapter()
        return dispatch.AgentRunner(
            store=store,
            launcher=self.resolver,
            run_id=run_id,
            story_id=story_id,
            card_id=card_id,
            adapters={adapter.name: adapter},
            harness_map={
                "resolver": models.HarnessAssignment(harness=adapter.name, model="fake-model")
            },
        )
```

in `test_a_conflict_resolves_the_same_way_on_the_pygents_engine` replace

```python
        {"workflow": "integrate", "run_id": RUN_ID, "story_id": "integrate", "card_id": STORY_B}
```

with

```python
        {"run_id": RUN_ID, "story_id": "integrate", "card_id": STORY_B}
```

in `test_resolve_conflict_walks_integrate_with_the_same_arguments` change the docstring's last clause `and the factory gets the YAML document."""` to `and the factory gets only the store and the three ids."""` and replace

```python
    (factory_call,) = factory_calls
    assert isinstance(factory_call["workflow"], loader.Workflow)
    assert factory_call["workflow"].name == "integrate"
    assert {key: value for key, value in factory_call.items() if key != "workflow"} == {
```

with

```python
    (factory_call,) = factory_calls
    assert factory_call == {
```

c. `tests/test_integrate_workflow.py`: replace the module docstring lines

```
Engine tier per design §14. Everything is real except the harness: the shipped
`builtin/integrate.yaml` against the default registry, `engine.run_subtask`,
```

with

```
Engine tier per design §14. Everything is real except the harness:
`workflow.integrate.INTEGRATE`, `runtime.engine.run_subtask`,
```

replace the import line `from agent_manager.workflow import load_builtin` with

```python
from agent_manager.runtime import engine as runtime_engine
from agent_manager.workflow import integrate as integrate_workflow
```

and in `_run` replace

```python
    """`engine.run_subtask(load_builtin("integrate"), ...)` for one synthetic
    subtask, with the real `dispatch.AgentRunner` as the agent runner."""
    workflow = load_builtin("integrate")
```

with

```python
    """`runtime.engine.run_subtask(INTEGRATE, ...)` for one synthetic subtask,
    with the real `dispatch.AgentRunner` as the agent runner."""
    workflow = integrate_workflow.INTEGRATE
```

delete the line `            workflow=workflow,` in its `dispatch.AgentRunner(...)` call, and replace `        summary = engine.run_subtask(` with `        summary = runtime_engine.run_subtask(`.

- [ ] **Step 5: Run the changed tests to see them fail**

Run: `uv run pytest tests/test_dispatch.py tests/test_engine.py tests/test_prompt.py tests/test_cli.py tests/test_integration.py tests/test_integrate_workflow.py -q`
Expected: FAIL with `TypeError: evaluate_gates() missing 1 required positional argument: 'warnings'` in `tests/test_dispatch.py`, `TypeError: AgentRunner.__init__() missing 1 required positional argument: 'workflow'` wherever an `AgentRunner` is built, and `TypeError: ... factory() got an unexpected keyword argument 'workflow'` for `_resume_factory` and `FakeFactory`.

- [ ] **Step 6: Remove the by-name path from `src/agent_manager/dispatch.py`**

a. Replace the module docstring's opening

```
`engine.run_subtask` resolves an agent phase's `inputs` and renders its prompt,
then hands `(phase, context, rendered)` to an injected `AgentPhaseRunner`
(`engine.py` lines 211-222). This module is that runner: the attempt directory,
the dispatch, the result file, the gates and the retry loop.
```

with

```
The pygents walk's `agent_phase` tool (`runtime/compile.py`) resolves an agent
phase's `inputs` and renders its prompt, then hands `(phase, context, rendered)`
to an injected `AgentPhaseRunner`. This module is that runner: the attempt
directory, the dispatch, the result file, the gates and the retry loop.
```

b. Delete the import line `from agent_manager.workflow.loader import AgentPhase, Workflow` and the block

```python
AnyAgentPhase = AgentPhase | phase_model.AgentPhase
"""Either agent-phase type: the YAML one (`result` and gates as names) or the
declared phase model (`result` a class, gates callables). Dispatch accepts both
while the YAML engine exists; nothing here imports pygents (rule 1)."""
```

Then replace-all `AnyAgentPhase` with `phase_model.AgentPhase`.

c. Replace the head of `evaluate_gates`, from `def evaluate_gates(` through the line `        kwargs = engine.bind_arguments(gate, values, phase=phase.name, function=name)`, with

```python
def evaluate_gates(
    phase: phase_model.AgentPhase,
    values: Mapping[str, Any],
    warnings: list[str],
) -> Verdict | None:
    """`None` when every gate passes, else the `gate_failed` verdict (§6 step 6).

    A gate returns `None` to pass, a mapping with `warn` to warn, or any other
    mapping to fail -- the contract `_evaluate_gates` already applies to
    deterministic phases. No per-gate retryable flag exists and this subtask
    does not add one: whether a `gate_failed` is retried is `retry.on`'s answer
    alone.

    The two ways a gate can be *wrong* rather than unhappy -- raising, or
    returning something that is not a mapping -- come back `fatal`, so no
    `retry.on` list can re-dispatch into a situation the harness cannot change.
    A binding failure is different again and propagates as `EngineError`: it
    means the workflow names a gate whose parameters nothing supplies, which is
    a bug in the workflow, not in the attempt.

    Every gate is the callable itself, used as-is and named by its `__name__`
    (its `repr` when it has none) in every message.
    """
    for gate in phase.gates:
        name = getattr(gate, "__name__", repr(gate))
        kwargs = engine.bind_arguments(gate, values, phase=phase.name, function=name)
```

d. In `class AgentRunner`, delete the field line `    workflow: Workflow`, and in `_attempt` replace

```python
            failure = evaluate_gates(
                phase,
                self.workflow,
                gate_values(context, phase.name, verdict.result),
                self.warnings,
            )
```

with

```python
            failure = evaluate_gates(
                phase,
                gate_values(context, phase.name, verdict.result),
                self.warnings,
            )
```

e. Check: `grep -n 'workflow\|Workflow\|AnyAgentPhase\|isinstance(entry, str)' src/agent_manager/dispatch.py` must print nothing.

- [ ] **Step 7: Stop threading the loaded workflow through `cli.py` and `integration.py`, and fix `prompt.py`'s Protocol docstring**

a. `src/agent_manager/cli.py`:
  - Delete the import lines `from agent_manager.workflow.loader import Workflow, load_builtin` and `from agent_manager.workflow.registry import WorkflowLoadError`.
  - In `RunnerFactory`'s docstring replace `the store, the workflow and three ids that do not exist until the run is` with `the store and three ids that do not exist until the run is`, and delete the line `        workflow: Workflow,` in its `__call__`.
  - In `default_runner_factory` delete the parameter line `    workflow: Workflow,`.
  - Replace-all the line `        workflow=workflow,` (8 spaces, with its newline) with nothing; it is in `default_runner_factory`'s `dispatch.AgentRunner(...)` call and in `drive_subtask`'s `factory(...)` call.
  - In `drive_subtask`, delete the line `    workflow = load_builtin(WORKFLOW_NAME)`.
  - In `HANDLED`, delete the line `    WorkflowLoadError,`.
  - Check: `grep -n 'workflow=\|Workflow\b\|load_builtin\|WorkflowLoadError' src/agent_manager/cli.py` prints only `workflow=WORKFLOW_NAME,` in `run_card`'s `models.Run(...)`.

b. `src/agent_manager/integration.py`:
  - Delete the import line `from agent_manager.workflow.loader import load_builtin`.
  - Delete the constant `WORKFLOW_NAME = "integrate"` and its docstring line.
  - In `_resolve_conflict`, delete the line `    workflow = load_builtin(WORKFLOW_NAME)` and the argument line `        workflow=workflow,` in its `runner_factory(...)` call.

c. `src/agent_manager/prompt.py`: replace

```python
    Structural, so both the YAML `workflow.loader.AgentPhase` and the declared
    `workflow.phases.AgentPhase` satisfy it without either being imported here.
```

with

```python
    Structural, so the declared `workflow.phases.AgentPhase` satisfies it
    without being imported here.
```

- [ ] **Step 8: Run the changed tests to see them pass**

Run: `uv run pytest tests/test_dispatch.py tests/test_engine.py tests/test_prompt.py tests/test_cli.py tests/test_integration.py tests/test_integrate_workflow.py -q`
Expected: PASS.

- [ ] **Step 9: Run the whole suite**

Run: `uv run pytest`
Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add src/agent_manager/dispatch.py src/agent_manager/cli.py src/agent_manager/integration.py src/agent_manager/prompt.py tests/test_dispatch.py tests/test_engine.py tests/test_prompt.py tests/test_cli.py tests/test_integration.py tests/test_integrate_workflow.py
git commit -m "Drop by-name dispatch and the loaded workflow the runner factory threaded"
```

---

### Task 3: Move the shared helpers into `runtime/` and delete the YAML engine

**Files:**
- Create: `src/agent_manager/runtime/errors.py`
- Create: `src/agent_manager/runtime/walk.py`
- Modify: `src/agent_manager/errors.py`, `src/agent_manager/runtime/engine.py`, `src/agent_manager/runtime/compile.py`, `src/agent_manager/runtime/checkpoint.py`, `src/agent_manager/runtime/context.py`, `src/agent_manager/dispatch.py`, `src/agent_manager/prompt.py`, `src/agent_manager/results.py`, `src/agent_manager/cli.py`, `src/agent_manager/integration.py`, `src/agent_manager/orchestrate.py`, `src/agent_manager/workflow/__init__.py`, `src/agent_manager/workflow/phases.py`, `src/agent_manager/workflow/task.py`, `src/agent_manager/workflow/integrate.py`, `pyproject.toml`, `uv.lock`
- Delete: `src/agent_manager/engine.py`, `src/agent_manager/workflow/loader.py`, `src/agent_manager/workflow/registry.py`, `src/agent_manager/workflow/builtin/task.yaml`, `src/agent_manager/workflow/builtin/integrate.yaml`
- Test: `tests/steps/test_verify.py`, `tests/test_results.py`, `tests/test_prompt.py`, `tests/test_dispatch.py`, `tests/test_engine.py`, `tests/test_cli.py`, `tests/test_orchestrate.py`, `tests/test_integration.py`, `tests/test_integrate_workflow.py`, `tests/runtime/test_compile.py`, `tests/runtime/test_checkpoint.py`, `tests/workflow/test_phases.py`, `tests/workflow/test_declared.py`, `tests/e2e/test_production_wiring.py`, `tests/e2e/test_parallel_milestone.py`, `tests/e2e/test_real_harness.py`; delete `tests/workflow/test_loader.py`, `tests/workflow/test_registry.py`, `tests/workflow/test_builtin_task.py`, `tests/workflow/test_builtin_integrate.py`

**Interfaces:**
- Consumes (from Task 2): `dispatch.evaluate_gates(phase, values, warnings)`, `AgentRunner` without `workflow`, `cli.RunnerFactory` without `workflow`, the `tests/test_engine.py` helpers.
- Produces:
  - `agent_manager.runtime.errors.EngineError(reason: str, *, phase=None, function=None, parameter=None)` — same class body as today.
  - `agent_manager.runtime.walk`: `RESERVED_CONTEXT_KEYS: tuple[str, ...]`, `subtask_context(subtask, repo_dir, commands=(), *, card=None, parent_story=None) -> dict[str, Any]`, `_document_paths(workflow: phase_model.Workflow, card) -> dict[str, str]`, `bind_arguments(fn, values, args=None, *, phase, function) -> dict[str, Any]`, `_utcnow() -> datetime`, `Clock`, `AgentPhaseRunner`, `SubtaskSummary`, `_Outcome`, `_gate_values(context, phase_name, result) -> dict`, `_bind_result(context, phase_name, result) -> None`, `run_one_step(*, phase: phase_model.Step, table, store, story_id, subtask, clock) -> _Outcome`, `_render_error(error) -> str`, `_record_phase(store, story_id, subtask, phase, status, started_at, ended_at, detail=None) -> None`, `_record_subtask_status(store, story_id, subtask, status) -> None`, `_escalate(summary, store, story_id, subtask, phase_name, detail) -> SubtaskSummary`, `_stop(summary, store, story_id, subtask, phase_name) -> SubtaskSummary`. `EngineError` is importable from it too (it imports the class).

- [ ] **Step 1: Point the tests at the new locations and delete the loader-tier tests**

a. `tests/steps/test_verify.py`: replace `from agent_manager import engine` with `from agent_manager.runtime import walk`, replace-all `engine.bind_arguments` with `walk.bind_arguments` (two calls and one comment).

b. `tests/test_results.py`: replace `from agent_manager.errors import EngineError` with `from agent_manager.runtime.errors import EngineError`.

c. `tests/test_prompt.py`: replace `from agent_manager.errors import EngineError` with `from agent_manager.runtime.errors import EngineError`, and delete `test_engine_re_exports_the_same_error_class`.

d. `tests/test_dispatch.py`: replace `from agent_manager.errors import AgentPhaseFailed, EngineError` with

```python
from agent_manager.errors import AgentPhaseFailed
from agent_manager.runtime.errors import EngineError
from agent_manager.runtime.walk import RESERVED_CONTEXT_KEYS
```

delete the line `    engine,` from the `from agent_manager import (...)` block, and replace `    assert "worktree" in engine.RESERVED_CONTEXT_KEYS` with `    assert "worktree" in RESERVED_CONTEXT_KEYS`.

e. `tests/test_engine.py`: replace `from agent_manager import dispatch, engine, models, results, store as store_module` with

```python
from agent_manager import dispatch, models, results, store as store_module
from agent_manager.runtime import walk
```

then replace-all ` engine.` with ` walk.`, `(engine.` with `(walk.` and `` `engine.`` with `` `walk.``. Check with `grep -n '[^_]engine\.' tests/test_engine.py` that only `new_engine.` remains.

f. `tests/test_cli.py`: replace

```python
from agent_manager.engine import SubtaskSummary
from agent_manager.errors import AgentPhaseFailed, EngineError
```

with

```python
from agent_manager.errors import AgentPhaseFailed
from agent_manager.runtime.errors import EngineError
from agent_manager.runtime.walk import SubtaskSummary
```

g. `tests/test_orchestrate.py`: replace

```python
from agent_manager import board, census, cli, dag, engine, integration, models, orchestrate, paths
from agent_manager import engine as engine_module
```

with

```python
from agent_manager import board, census, cli, dag, integration, models, orchestrate, paths
from agent_manager.runtime.walk import SubtaskSummary
```

replace-all `engine_module.SubtaskSummary(` with `SubtaskSummary(`, and in the docstring that begins `"""`engine._stop` writes "stopped before <phase>"` replace `` `engine._stop` `` with `` `walk._stop` ``.

h. `tests/test_integration.py`: replace `from agent_manager.engine import SubtaskSummary` with `from agent_manager.runtime.walk import SubtaskSummary`.

i. `tests/test_integrate_workflow.py`: replace

```python
from agent_manager import dispatch, engine, models, prompt
from agent_manager import store as store_module
from agent_manager.errors import EngineError
```

with

```python
from agent_manager import dispatch, models, prompt
from agent_manager import store as store_module
from agent_manager.runtime.errors import EngineError
from agent_manager.runtime.walk import SubtaskSummary
```

and replace `    summary: engine.SubtaskSummary` with `    summary: SubtaskSummary`.

j. `tests/runtime/test_compile.py`: replace `from agent_manager.errors import AgentPhaseFailed, EngineError` with

```python
from agent_manager.errors import AgentPhaseFailed
from agent_manager.runtime.errors import EngineError
```

k. `tests/runtime/test_checkpoint.py`: replace `from agent_manager.errors import EngineError` with `from agent_manager.runtime.errors import EngineError`, and in its docstring replace `engine.py `run_one_step`` with `runtime/walk.py `run_one_step``.

l. `tests/workflow/test_phases.py`: replace the import block

```python
from agent_manager import results
from agent_manager.steps import plan_check as plan_check_module
from agent_manager.steps import reducers, rollup
from agent_manager.workflow import phases as phases_module
from agent_manager.workflow.loader import load_builtin, load_workflow
from agent_manager.workflow.phases import (
    AgentPhase,
    Goto,
    Retry,
    Step,
    Workflow,
    WorkflowError,
    from_loader,
)
from agent_manager.workflow.registry import (
    FunctionRegistry,
    UnknownFunctionError,
    default_registry,
)
```

with

```python
from agent_manager.workflow import phases as phases_module
from agent_manager.workflow.phases import (
    AgentPhase,
    Goto,
    Retry,
    Step,
    Workflow,
    WorkflowError,
)
```

and delete everything from the line `# --- from_loader (card 0326732a) -------------------------------------------` to the end of the file.

m. `tests/workflow/test_declared.py` (trimmed, not deleted — see "Deviations"): replace the module docstring and imports (lines 1-16) with

```python
"""Pure-data tier (spec §9): TASK and INTEGRATE are constant phase-model data,
so these are plain unit tests -- no fakes, no git, no harness. The only I/O is
validate()'s role loading."""

import ast
from datetime import timedelta
from pathlib import Path

from agent_manager import dispatch
from agent_manager.workflow import integrate as integrate_module
from agent_manager.workflow import task as task_module
from agent_manager.workflow.phases import AgentPhase, Goto
from agent_manager.workflow.task import TASK, LAUNCHER_TIMEOUT
from agent_manager.workflow.integrate import INTEGRATE
```

then delete `DECLARED_ONLY_INPUTS` with its docstring, `_declared_only`, `_shipped`, `_assert_same_callables`, `test_task_equals_the_shipped_yaml`, `test_integrate_equals_the_shipped_yaml`, `test_task_callables_are_the_registry_bindings` and `test_integrate_callables_are_the_registry_bindings`. Keep `_imported_modules` and the other nine tests.

n. Before deleting `tests/workflow/test_builtin_integrate.py`, compare it with `tests/test_integrate_workflow.py`, which walks the declared `INTEGRATE` end to end. Covered there by behaviour: resolve runs before verify, resolve is the `resolver` role, it is retried twice on `gate_failed` and on `schema_invalid`, `merge_completed_gate` judges it and names itself in the feedback, the `merge_tip`, `branch` and `base_branch` sections reach the brief, and verify is judged by `verification_passed_gate`. Not covered anywhere once the file goes: that `INTEGRATE` has no `worktree` phase; that the resolve brief carries the `conflict_files` and `verification` sections with those exact bodies; that resolve declares no `writes`; that neither `merge_tip` nor `conflict_files` is a reserved key or produced by another phase; that INTEGRATE's verify step equals TASK's. Put this list, as written here after you have checked it, in the body of this task's commit message; do not write new tests for it.

o. Delete the four loader-tier test files:

```bash
git rm tests/workflow/test_loader.py tests/workflow/test_registry.py tests/workflow/test_builtin_task.py tests/workflow/test_builtin_integrate.py
```

p. The e2e modules. In `tests/e2e/test_production_wiring.py` replace

```python
from agent_manager import board, cli, prompt, results
from agent_manager.steps import docs_commit
from agent_manager.workflow import load_builtin
```

with

```python
from agent_manager import board, cli, prompt
from agent_manager.steps import docs_commit
from agent_manager.workflow import task as task_workflow
```

replace-all `    workflow = load_builtin("task")` with `    workflow = task_workflow.TASK`, and in `test_the_brief_carries_the_result_path_and_the_schema` replace

```python
        declared = workflow.phase(name).result
        model = results.RESULT_MODELS[declared]
```

with

```python
        model = workflow.phase(name).result
```

In `tests/e2e/test_parallel_milestone.py` replace `from agent_manager.workflow.loader import load_builtin` with `from agent_manager.workflow import task as task_workflow` and `    phase_names = load_builtin(cli.WORKFLOW_NAME).phase_names` with `    phase_names = task_workflow.TASK.phase_names`. In `tests/e2e/test_real_harness.py` replace

```python
from agent_manager import board, cli, results
from agent_manager.workflow import load_builtin
```

with

```python
from agent_manager import board, cli
from agent_manager.workflow import task as task_workflow
```

and replace

```python
    workflow = load_builtin("task")
    validated: set[str] = set()
    for name, attempt in sorted(agent_attempts.items()):
        assert attempt.result_path is not None, name
        path = Path(attempt.result_path)
        assert path.is_file(), (name, path)
        model = results.resolve_result_model(
            workflow.phase(name).result, results.RESULT_MODELS, phase=name
        )
```

with

```python
    validated: set[str] = set()
    for name, attempt in sorted(agent_attempts.items()):
        assert attempt.result_path is not None, name
        path = Path(attempt.result_path)
        assert path.is_file(), (name, path)
        model = task_workflow.TASK.phase(name).result
```

- [ ] **Step 2: Run the repointed tests to see them fail**

Run: `uv run pytest tests/steps/test_verify.py tests/test_results.py tests/test_engine.py -q`
Expected: FAIL at collection with `ModuleNotFoundError: No module named 'agent_manager.runtime.walk'` and `No module named 'agent_manager.runtime.errors'`.

- [ ] **Step 3: Create `src/agent_manager/runtime/errors.py`**

```python
"""`EngineError`, importable without pygents (rule 1).

The exception `prompt.py`, `dispatch.py`, `results.py` and the pygents walk
raise for a wiring or workflow bug. It lives in a module that imports nothing,
so the modules below the runtime can raise it without an import cycle and
without loading pygents; `runtime/__init__.py` stays import-free for the same
reason.
"""


class EngineError(RuntimeError):
    """The engine refused to run, or could not make sense of, a phase.

    Carries the coordinates an operator needs to find the offending line of the
    workflow document: which phase, which registered function, which parameter.
    Prompt rendering reuses `parameter` for the name of the declared input it
    could not resolve -- the input name is what an operator greps the document
    for, exactly as a parameter name is.
    """

    def __init__(
        self,
        reason: str,
        *,
        phase: str | None = None,
        function: str | None = None,
        parameter: str | None = None,
    ) -> None:
        self.reason = reason
        self.phase = phase
        self.function = function
        self.parameter = parameter
        parts = []
        if phase is not None:
            parts.append(f"phase {phase!r}")
        if function is not None:
            parts.append(f"function {function!r}")
        if parameter is not None:
            parts.append(f"parameter {parameter!r}")
        prefix = ", ".join(parts)
        super().__init__(f"{prefix}: {reason}" if prefix else reason)
```

- [ ] **Step 4: Create `src/agent_manager/runtime/walk.py`**

The bodies are `engine.py`'s, verbatim, with the loader half removed: `run_one_step`, `_evaluate_gates` and `_skip_target` lose their `workflow` parameter and call the phase's callables directly (a `phases.Step` always holds callables, so `_resolve` has nothing left to resolve), `_document_paths`/`_writing_phase` check only `phases.AgentPhase`, and `run_subtask`, `_start_index` and `_run_deterministic` (the yaml walk) do not move.

```python
"""The pieces of the subtask walk that live below pygents (design §6, G10).

`runtime/engine.py` walks one subtask on pygents. What that walk, the two
compiled tools (`runtime/compile.py`) and the agent-phase dispatcher
(`dispatch.py`) share lives here: the binding table (`subtask_context`,
`_document_paths`, `RESERVED_CONTEXT_KEYS`), binding by parameter name
(`bind_arguments`), one deterministic step run, judged and recorded
(`run_one_step`), and the summary and its subtask rows (`SubtaskSummary`,
`_escalate`, `_stop`, `_record_subtask_status`).

No pygents import here (rule 1): `dispatch.py` imports this module, and
dispatch must never load pygents. `runtime/__init__.py` imports nothing, so
importing this module loads nothing else from `runtime/`.

§6 says a step is called as `run(ctx) -> dict`, but the real steps take named
keyword arguments (`worktree.ensure(branch, base, worktree, repo_dir)`,
`verify.run_suite(commands, worktree)`, `plan_check.find_validated_plan(card)`).
Rather than rewrite four working steps, a step is bound by parameter name out
of a per-subtask context mapping overlaid with the phase's declared `args`, and
a binding failure raises `EngineError` naming phase, function and parameter
before the call -- a bare `TypeError` from a call site tells an operator
nothing about which phase is wrong.
"""

import inspect
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from agent_manager import models, prompt
from agent_manager.runtime.errors import EngineError
from agent_manager.store import Store
from agent_manager.workflow import phases as phase_model

_EMPTY = inspect.Parameter.empty
_VARIADIC = (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)

RESERVED_CONTEXT_KEYS = (
    "card",
    "card_details",
    "parent_story_details",
    "branch",
    "base",
    "base_branch",
    "worktree",
    "repo_dir",
    "commands",
    "spec_path",
    "plan_path",
)
"""The context keys the engine owns, and no phase result may replace.

`subtask_context` always sets all but `spec_path` and `plan_path`; those two are
set by `_document_paths` only when some agent phase in the workflow declares
them as inputs. Reserved either way: a key the engine may set is a key a phase
result must never take over, whether this particular workflow made it appear or
not.

Named as a constant because phase results land in the same mapping under the
phase's name: a phase called `worktree` -- the shipped `task` workflow has
exactly one -- would otherwise overwrite the real worktree path every later
step binds from, and a phase called `spec_path` would overwrite the document
path `implement` and `review` both declare. `_bind_result` uses this to skip
writing such a result back into the table rather than refuse the phase
outright.
"""


def subtask_context(
    subtask: models.SubtaskRun,
    repo_dir: Path,
    commands: Sequence[str] = (),
    *,
    card: models.Card | None = None,
    parent_story: models.Card | None = None,
) -> dict[str, Any]:
    """The starting binding table for one subtask's phases.

    The keys are the *callables'* parameter names, not the model's field names:
    binding is by name, and no step in `workflow.task.TASK` declares `args`
    that could bridge the difference. Hence `base` for `base_branch` and
    `worktree` for `worktree_path`.

    `base_branch` is that same string under a second key, because the two sides
    of the workflow disagree about the name: `worktree.ensure(branch, base,
    ...)` asks for `base`, and `reducers.review_gate(review, branch,
    base_branch)` asks for `base_branch`. A declared `args` entry cannot bridge
    it -- `args` are literals and the base branch is per-run -- and renaming
    either parameter would change a shipped step or a ported gate. Both keys are
    reserved, so no phase result can make them disagree.

    `card` stays the bare id string every deterministic step binds by that name
    (`plan_check.find_validated_plan(card)`). The full cards the §7 `card` and
    `parent_story` *inputs* render live beside it under `card_details` and
    `parent_story_details`, supplied by the caller exactly as `commands` is --
    nothing here reads the board.
    """
    return {
        "card": subtask.card_id,
        "card_details": card,
        "parent_story_details": parent_story,
        "branch": subtask.branch,
        "base": subtask.base_branch,
        "base_branch": subtask.base_branch,
        "worktree": subtask.worktree_path,
        "repo_dir": repo_dir,
        "commands": list(commands),
    }


_DOCUMENT_INPUTS = {"spec_path": "spec", "plan_path": "plan"}
"""Which phase's `writes` template each §7 document-path input comes from.

Keyed on the phase *name*, not on a guess about the path: `TASK` names them
`spec` and `plan`, and matching on the template text would make a workflow
whose plan phase writes into `docs/specs/` resolve backwards.
"""


def _document_paths(
    workflow: phase_model.Workflow, card: models.Card | None
) -> dict[str, str]:
    """`spec_path` / `plan_path` for the whole subtask, computed once, from the workflow.

    Computed at subtask start rather than when the `spec` and `plan` phases run:
    `plan_check` may `skip_to` `docs_commit`, and `implement` still declares both
    inputs. §7 calls them "paths in the repo, already committed" -- the path is a
    property of the card and the workflow, not of a phase having executed.
    """
    declared = {
        name
        for phase in workflow.phases
        if isinstance(phase, phase_model.AgentPhase)
        for name in phase.inputs
        if name in _DOCUMENT_INPUTS
    }
    paths: dict[str, str] = {}
    for name in sorted(declared):
        source = _writing_phase(workflow, _DOCUMENT_INPUTS[name], name)
        if card is None:
            raise EngineError(
                f"is declared as an input, but no card was supplied to expand "
                f"{source.writes!r} (the stem comes from the card's id and title)",
                phase=source.name,
                parameter=name,
            )
        paths[name] = prompt.expand_writes(
            source.writes, card, phase=source.name, input_name=name
        )
    return paths


def _writing_phase(
    workflow: phase_model.Workflow, phase_name: str, input_name: str
) -> phase_model.AgentPhase:
    found = next((p for p in workflow.phases if p.name == phase_name), None)
    if not isinstance(found, phase_model.AgentPhase) or found.writes is None:
        raise EngineError(
            f"is declared as an input, but this workflow has no agent phase named "
            f"{phase_name!r} with a `writes:` template to take the path from "
            f"(phases: {', '.join(workflow.phase_names)})",
            parameter=input_name,
        )
    return found


def bind_arguments(
    fn: Callable[..., Any],
    values: Mapping[str, Any],
    args: Mapping[str, Any] | None = None,
    *,
    phase: str,
    function: str,
) -> dict[str, Any]:
    """Keyword arguments for `fn`, taken by name from `values` overlaid with `args`.

    Only parameters `fn` actually declares are passed, so a context holding
    twenty keys still calls a two-parameter step with two. `*args`/`**kwargs`
    are ignored rather than fed: a step that declares `**kwargs` has not asked
    for the whole context.
    """
    args = {} if args is None else args
    parameters = inspect.signature(fn).parameters
    for key in args:
        if key not in parameters:
            raise EngineError(
                f"the document declares args key {key!r}, which this function does not "
                f"take (it takes: {', '.join(parameters) or 'nothing'})",
                phase=phase,
                function=function,
                parameter=key,
            )
    supplied = {**values, **args}
    bound: dict[str, Any] = {}
    for parameter in parameters.values():
        if parameter.kind in _VARIADIC:
            continue
        if parameter.name not in supplied:
            if parameter.default is _EMPTY:
                raise EngineError(
                    "no value for a required parameter "
                    f"(available: {', '.join(sorted(supplied)) or 'nothing'})",
                    phase=phase,
                    function=function,
                    parameter=parameter.name,
                )
            continue
        if parameter.kind is inspect.Parameter.POSITIONAL_ONLY:
            raise EngineError(
                "is positional-only, and the engine binds every argument by name",
                phase=phase,
                function=function,
                parameter=parameter.name,
            )
        bound[parameter.name] = supplied[parameter.name]
    return bound


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


Clock = Callable[[], datetime]

AgentPhaseRunner = Callable[
    [phase_model.AgentPhase, Mapping[str, Any], prompt.RenderedPrompt], Any
]
"""The seam `dispatch.AgentRunner` fills: `(phase, context, rendered) -> result`.

The walk resolves the phase's declared `inputs` and renders the prompt before
the call, because that is exactly where §6 puts step 2 -- and because the runner
cannot dispatch without a prompt it can write to the attempt directory first.
Everything past this call -- that directory, dispatch, schema validation, retry,
its gates -- belongs to the runner. The walk only takes the returned result
into the pool under the phase's name.
"""


@dataclass
class SubtaskSummary:
    """What the walk did to one subtask.

    Returned rather than raised: a caller must be able to tell a clean `done`
    from a `done` whose board write silently failed (§12), and an exception
    carries neither the results nor the warnings.
    """

    status: Literal["done", "escalated", "stopped"] = "done"
    results: dict[str, Any] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    failed_phase: str | None = None
    detail: str | None = None


@dataclass
class _Outcome:
    """One deterministic phase's verdict, already recorded."""

    ok: bool
    result: Any = None
    detail: str | None = None
    warnings: list[str] = field(default_factory=list)
    skip_to: str | None = None


class _GateFailed(Exception):
    """A gate returned a verdict. Private: it never leaves `run_one_step`."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


def _gate_values(
    context: Mapping[str, Any], phase_name: str, result: Mapping[str, Any]
) -> dict[str, Any]:
    """The binding table a gate or a `when` predicate sees.

    The result appears twice on purpose: under the phase's name, which is how
    §6 says later phases read it, and under `result`, which is the parameter
    name `plan_check.has_validated_plan(result)` and the shipped gates use.

    A phase named after a reserved key is the one exception, for the same
    reason `_bind_result` is: a gate on the `worktree` phase that binds
    `worktree` wants the path the phase was pointed at, not that phase's
    return value. `result` still reaches it either way.
    """
    values = {**context, "result": result}
    if phase_name not in RESERVED_CONTEXT_KEYS:
        values[phase_name] = result
    return values


def _label(fn: Callable[..., Any]) -> str:
    """How messages name a callable: its `__name__`, or its `repr` when it has none."""
    return getattr(fn, "__name__", repr(fn))


def _evaluate_gates(
    phase: phase_model.Step,
    values: Mapping[str, Any],
    warnings: list[str],
) -> None:
    """Run every gate in order; append warnings, raise `_GateFailed` on a verdict."""
    for gate in phase.gates:
        name = _label(gate)
        kwargs = bind_arguments(gate, values, phase=phase.name, function=name)
        verdict = gate(**kwargs)
        if verdict is None:
            continue
        if not isinstance(verdict, Mapping):
            raise EngineError(
                f"gate returned {type(verdict).__name__}; a gate returns None to pass "
                "or a mapping verdict to fail, and anything else would be read as a "
                "pass by accident",
                phase=phase.name,
                function=name,
            )
        if "warn" in verdict:
            warnings.append(
                f"phase {phase.name!r} gate {name!r} warned: {verdict['warn']}"
            )
            continue
        raise _GateFailed(f"phase {phase.name!r} gate {name!r} failed: {_render_verdict(verdict)}")


def _render_verdict(verdict: Mapping[str, Any]) -> str:
    return ", ".join(f"{key}={value}" for key, value in sorted(verdict.items()))


def _skip_target(phase: phase_model.Step, values: Mapping[str, Any]) -> str | None:
    """The phase to jump to, or `None` to fall through to the next one.

    Both `when` and `skip_to` are required for a jump: `when` alone has nowhere
    to go, and `skip_to` alone would be an unconditional jump the workflow
    author did not write.
    """
    if phase.when is None or phase.skip_to is None:
        return None
    kwargs = bind_arguments(
        phase.when, values, phase=phase.name, function=_label(phase.when)
    )
    return phase.skip_to if phase.when(**kwargs) else None


def _bind_result(context: dict[str, Any], phase_name: str, result: Any) -> None:
    """Fold one phase's result into the binding table under its own name.

    `TASK` names its worktree-setup phase `worktree`, exactly the key
    `subtask_context` binds the real worktree path under. The result is still
    recorded and returned in the summary either way; it is just never written
    back here, so the reserved value survives for every later phase that binds
    `worktree` (or any other reserved key) by name, instead of being silently
    replaced by a same-named phase's own result. `runtime/context.py`'s
    `binding_table` applies the same rule to the pygents pool.
    """
    if phase_name not in RESERVED_CONTEXT_KEYS:
        context[phase_name] = result


def run_one_step(
    *,
    phase: phase_model.Step,
    table: Mapping[str, Any],
    store: Store,
    story_id: str,
    subtask: models.SubtaskRun,
    clock: Clock,
) -> _Outcome:
    """One deterministic phase, run, judged and recorded.

    The pygents engine's `step_phase` tool calls this through
    `bridge.call_step`: binding, the mapping check, gates, `when` and
    `skip_to`, and the `started`/`done`/`failed` phase rows. `best_effort` is
    the caller's to apply -- this returns the verdict, not the walk's reaction
    to it.
    """
    started_at = clock()
    _record_phase(store, story_id, subtask, phase, "started", started_at, None)
    warnings: list[str] = []
    try:
        label = _label(phase.run)
        kwargs = bind_arguments(
            phase.run, table, phase.args, phase=phase.name, function=label
        )
        result = phase.run(**kwargs)
        if not isinstance(result, Mapping):
            raise EngineError(
                f"returned {type(result).__name__}, but a deterministic phase must "
                "return a mapping: a gate or a later `when` would read anything else "
                "as closed and the run would branch wrongly",
                phase=phase.name,
                function=label,
            )
        _evaluate_gates(phase, _gate_values(table, phase.name, result), warnings)
        skip_to = _skip_target(phase, _gate_values(table, phase.name, result))
    except _GateFailed as failure:
        _record_phase(
            store, story_id, subtask, phase, "failed", started_at, clock(), failure.detail
        )
        return _Outcome(ok=False, detail=failure.detail, warnings=warnings)
    except Exception as error:
        # Deliberately total. A step is other people's code -- GitError, OSError,
        # anything -- and an exception escaping the walk would leave the subtask
        # recorded `started` forever, which is exactly what resume mistakes for
        # work in flight.
        detail = _render_error(error)
        _record_phase(
            store, story_id, subtask, phase, "failed", started_at, clock(), detail
        )
        return _Outcome(ok=False, detail=detail, warnings=warnings)
    _record_phase(store, story_id, subtask, phase, "done", started_at, clock())
    return _Outcome(ok=True, result=result, warnings=warnings, skip_to=skip_to)


def _render_error(error: BaseException) -> str:
    return f"{type(error).__name__}: {error}"


def _record_phase(
    store: Store,
    story_id: str,
    subtask: models.SubtaskRun,
    phase: phase_model.Step,
    status: models.Status,
    started_at: datetime,
    ended_at: datetime | None,
    detail: str | None = None,
) -> None:
    store.record_phase(
        story_id,
        subtask.card_id,
        models.PhaseRun(
            name=phase.name,
            kind="deterministic",
            status=status,
            started_at=started_at,
            ended_at=ended_at,
            detail=detail,
        ),
    )


def _record_subtask_status(
    store: Store, story_id: str, subtask: models.SubtaskRun, status: models.Status
) -> None:
    store.record_subtask(story_id, subtask.model_copy(update={"status": status}))


def _escalate(
    summary: SubtaskSummary,
    store: Store,
    story_id: str,
    subtask: models.SubtaskRun,
    phase_name: str,
    detail: str | None,
) -> SubtaskSummary:
    """Record the subtask `escalated` and hand the walk's summary back.

    One helper for both phase kinds, because §12's "escalation stops the run" is
    one rule: the summary is returned rather than raised so the caller can still
    read the results and warnings of everything that ran before it.
    """
    summary.status = "escalated"
    summary.failed_phase = phase_name
    summary.detail = detail
    _record_subtask_status(store, story_id, subtask, "escalated")
    return summary


def _stop(
    summary: SubtaskSummary,
    store: Store,
    story_id: str,
    subtask: models.SubtaskRun,
    phase_name: str,
) -> SubtaskSummary:
    """Record the subtask `stopped` before `phase_name` and hand the summary back.

    Kept apart from `_escalate` on purpose: `stopped` is not `failed`, so
    `failed_phase` stays `None`. Results, warnings and skips gathered so far
    stay on the summary.
    """
    summary.status = "stopped"
    summary.detail = f"stopped before {phase_name}"
    _record_subtask_status(store, story_id, subtask, "stopped")
    return summary
```

- [ ] **Step 5: Repoint `src/` at `runtime/walk.py` and `runtime/errors.py`**

a. `src/agent_manager/errors.py`: replace the module docstring (lines 1-7) with

```python
"""`AgentPhaseFailed`: how an agent phase's terminal failure leaves the runner.

`EngineError` lives in `agent_manager.runtime.errors`, importable without
pygents.
"""
```

delete the whole `class EngineError` (keep the two blank lines before `class AgentPhaseFailed`), and in `AgentPhaseFailed`'s docstring replace

```
    Raised rather than returned because `engine.AgentPhaseRunner` is typed
    `(phase, context, rendered) -> result`: there is no second channel in that
    signature, and returning a sentinel result would be indistinguishable from a
    phase whose harness genuinely produced one. `run_subtask` catches it and
    turns it into `summary.status = "escalated"` -- the same edge the
    deterministic branch reaches through `_Outcome(ok=False, ...)`.
```

with

```
    Raised rather than returned because `runtime.walk.AgentPhaseRunner` is typed
    `(phase, context, rendered) -> result`: there is no second channel in that
    signature, and returning a sentinel result would be indistinguishable from a
    phase whose harness genuinely produced one. The pygents walk's `agent_phase`
    tool catches it: a critic with `on_fail` loops back, anything else escalates
    -- the same edge a step reaches through `_Outcome(ok=False, ...)`.
```

b. `src/agent_manager/runtime/engine.py`: replace `from agent_manager import engine as old` with `from agent_manager.runtime import walk`, replace-all `old.` with `walk.`, and replace the module docstring's second paragraph

```
`run_subtask` is the old engine's `run_subtask` with the walk replaced. The
binding table, the escalation and the final subtask row are the old engine's
own helpers, called exactly as it calls them, so a summary, a journal line or
a phase row cannot tell the two engines apart (G10). What differs is the walk:
the workflow is compiled into two pygents tools, one `Agent` runs them, and
`agent.run()` is always consumed to the end -- never broken or returned out of.
```

with

```
The binding table, the escalation and the final subtask row are
`runtime/walk.py`'s helpers, so a summary, a journal line or a phase row keeps
the shape it has always had (G10). The workflow is compiled into two pygents
tools, one `Agent` runs them, and `agent.run()` is always consumed to the end
-- never broken or returned out of.
```

Check with `grep -n 'old\b\|walk\.' src/agent_manager/runtime/engine.py` that every former `old.` reference is now `walk.` and no `old` name is left outside comments.

c. `src/agent_manager/runtime/compile.py`: replace

```python
from agent_manager import engine as old_engine
from agent_manager import prompt
from agent_manager.errors import AgentPhaseFailed, EngineError
from agent_manager.runtime import bridge, context
```

with

```python
from agent_manager import prompt
from agent_manager.errors import AgentPhaseFailed
from agent_manager.runtime import bridge, context, walk
from agent_manager.runtime.errors import EngineError
```

and replace-all `old_engine.` with `walk.` (two sites: `_render_error` and `run_one_step`).

d. `src/agent_manager/runtime/checkpoint.py`: replace `from agent_manager import engine as old` with `from agent_manager.runtime import walk` and `        saved_at=old._utcnow(),` with `        saved_at=walk._utcnow(),`.

e. `src/agent_manager/runtime/context.py`: replace `from agent_manager.engine import RESERVED_CONTEXT_KEYS` with `from agent_manager.runtime.walk import RESERVED_CONTEXT_KEYS`, and in `binding_table`'s docstring replace `it, but never replaces the engine's own value in the table: the pygents` / `twin of `engine._bind_result`.` with `it, but never replaces the engine's own value in the table: the rule` / ``walk._bind_result` applies, over the pool.``.

f. `src/agent_manager/dispatch.py`: replace

```python
from agent_manager import engine, models, paths, prompt, results
from agent_manager.errors import AgentPhaseFailed, EngineError
```

with

```python
from agent_manager import models, paths, prompt, results
from agent_manager.errors import AgentPhaseFailed
from agent_manager.runtime.errors import EngineError
from agent_manager.runtime.walk import RESERVED_CONTEXT_KEYS, bind_arguments
```

replace `    if phase_name not in engine.RESERVED_CONTEXT_KEYS:` with `    if phase_name not in RESERVED_CONTEXT_KEYS:`, replace `        kwargs = engine.bind_arguments(gate, values, phase=phase.name, function=name)` with `        kwargs = bind_arguments(gate, values, phase=phase.name, function=name)`, and fix three docstrings/comments: `The same table `engine._gate_values` builds for a deterministic phase, and` → `The same table `walk._gate_values` builds for a deterministic phase, and`; `"""`engine._render_error`'s format, so both phase kinds fail the same way."""` → `"""`walk._render_error`'s format, so both phase kinds fail the same way."""`; `"""One agent phase, run to a terminal outcome: `engine.AgentPhaseRunner`.` → `"""One agent phase, run to a terminal outcome: `walk.AgentPhaseRunner`.`; and `            # Symmetric with `engine._run_deterministic`, which records its own` → `            # Symmetric with `walk.run_one_step`, which records its own`.

g. `src/agent_manager/prompt.py` and `src/agent_manager/results.py`: in each, replace `from agent_manager.errors import EngineError` with `from agent_manager.runtime.errors import EngineError`.

h. `src/agent_manager/cli.py`: delete the line `    engine,` from the `from agent_manager import (...)` block; replace `from agent_manager.errors import EngineError` with

```python
from agent_manager.runtime.errors import EngineError
from agent_manager.runtime.walk import AgentPhaseRunner, SubtaskSummary
```

replace-all `engine.AgentPhaseRunner` with `AgentPhaseRunner` (the `RunnerFactory` docstring and return annotation, and `default_runner_factory`'s return annotation) and replace `    summary: engine.SubtaskSummary` with `    summary: SubtaskSummary`. Check: `grep -n '\bengine\.' src/agent_manager/cli.py` prints nothing.

i. `src/agent_manager/integration.py`: replace `from agent_manager import cli, dag, engine, models` with

```python
from agent_manager import cli, dag, models
```

add `from agent_manager.runtime.walk import SubtaskSummary` after `from agent_manager.runtime import engine as runtime_engine`, and replace-all `engine.SubtaskSummary` with `SubtaskSummary` (the return annotation of `_resolve_conflict` and the `summary` parameter of `_resolver_detail`). Leave `runtime_engine.` untouched.

j. `src/agent_manager/orchestrate.py`: replace-all `` `engine._stop` `` with `` `walk._stop` `` (the `STOPPED_PREFIX` docstring and `stopped_before_phase`'s docstring).

- [ ] **Step 6: Delete the YAML engine, the loader, the registry, the builtin documents and `from_loader`**

a. Delete the files:

```bash
git rm src/agent_manager/engine.py src/agent_manager/workflow/loader.py src/agent_manager/workflow/registry.py src/agent_manager/workflow/builtin/task.yaml src/agent_manager/workflow/builtin/integrate.yaml
```

b. Replace the whole of `src/agent_manager/workflow/__init__.py` with

```python
"""The workflow as declared Python data.

`workflow.phases` is the phase model; `workflow.task.TASK` and
`workflow.integrate.INTEGRATE` are the two shipped workflows. Nothing here
executes a phase: `runtime/engine.py` walks a workflow, `prompt.py` renders an
agent phase's inputs and `dispatch.py` runs one. Nothing is re-exported, so
importing `agent_manager.workflow.phases` loads nothing else.
"""
```

c. `src/agent_manager/workflow/phases.py`: replace `from typing import TYPE_CHECKING, Any, Callable, Mapping` with `from typing import Any, Callable, Mapping`, delete the two lines

```python
if TYPE_CHECKING:
    from agent_manager.workflow import loader
```

(and the blank lines around them, leaving two blank lines before `class WorkflowError`), and delete the whole function `from_loader` at the end of the file.

d. `src/agent_manager/workflow/task.py`: replace the module docstring's first two paragraphs

```
Pinned to `builtin/task.yaml`: `tests/workflow/test_declared.py` asserts that
`TASK.digest()` equals the digest of the shipped YAML run through
`phases.from_loader`, so the two cannot drift apart silently. Every callable
is the real function object `registry.default_registry()` binds -- never a
registry lookup -- because the digest names callables by `module.qualname`.

Timeouts, the critics' `on_fail` loops and the `feedback` input are the data
the YAML never had; the pinning test copies each from here and nothing else.
```

with

```
Every callable is the real function object, never a name looked up at run
time, because the digest names callables by `module.qualname` and a resumed
run must be able to tell whether the workflow it checkpointed is this one.
`tests/workflow/test_declared.py` pins the timeouts, the critics' `on_fail`
loops and that the workflow validates.
```

replace the sentence

```
and renders no section, so a clean run's briefs are unchanged. Only the
pygents engine reads `on_fail`: `--engine yaml` walks `builtin/task.yaml` and
still escalates on the first block.
```

with

```
and renders no section, so a clean run's briefs are unchanged.
```

and replace `"""`builtin/task.yaml`, phase for phase."""` with `"""The fourteen-phase task workflow of design §5, phase for phase."""`.

e. `src/agent_manager/workflow/integrate.py`: replace

```
Pinned to `builtin/integrate.yaml` by `tests/workflow/test_declared.py`, the
same way `workflow.task.TASK` is pinned to `task.yaml`. The timeout floor is
`workflow.task`'s -- one launcher timeout for every declared workflow.
```

with

```
The timeout floor is `workflow.task`'s -- one launcher timeout for every
declared workflow.
```

and replace `"""`builtin/integrate.yaml`, phase for phase."""` with `"""Resolve, then verify (Integrate addendum I3)."""`.

- [ ] **Step 7: Drop `pyyaml`**

a. `pyproject.toml`: delete the line `    "pyyaml>=6.0.2",` and replace the comment

```
# importlib mode, not the default prepend mode: tests/workflow/test_loader.py and
# tests/roles/test_loader.py share a basename, and prepend mode names test modules
# after their basename alone, so collecting both aborts with "import file mismatch".
```

with

```
# importlib mode, not the default prepend mode: tests/steps/test_integrate.py and
# tests/e2e/test_integrate.py share a basename, and prepend mode names test modules
# after their basename alone, so collecting both aborts with "import file mismatch".
```

b. Run: `uv lock`
Expected: `uv.lock` is rewritten without `agent-manager`'s direct `pyyaml` dependency (it may stay in the lock only if another dependency needs it).

c. Run: `grep -rn "import yaml\|from yaml" src tests`
Expected: no output.

- [ ] **Step 8: Run the repointed tests to see them pass**

Run: `uv run pytest tests/steps/test_verify.py tests/test_results.py tests/test_prompt.py tests/test_dispatch.py tests/test_engine.py tests/runtime tests/workflow -q`
Expected: PASS.

- [ ] **Step 9: Run the whole suite**

Run: `uv run pytest`
Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add -A src/agent_manager tests pyproject.toml uv.lock
git commit -m "Move the shared walk helpers into runtime/ and delete the YAML engine" -m "tests/workflow/test_builtin_integrate.py is deleted with builtin/integrate.yaml. tests/test_integrate_workflow.py covers INTEGRATE's resolve-then-verify order, the resolver role, two attempts on gate_failed and schema_invalid, merge_completed_gate and its feedback, the merge_tip/branch/base_branch brief sections and verification_passed_gate. No longer covered: INTEGRATE has no worktree phase; the conflict_files and verification brief sections; resolve declares no writes; merge_tip and conflict_files are neither reserved nor produced by a phase; INTEGRATE's verify equals TASK's."
```

---

### Task 4: Fix the docstrings that name deleted code, and check the done-when list

No behaviour changes here. Every edit is a docstring or comment that still names something Tasks 1-3 deleted.

**Files:**
- Modify: `src/agent_manager/cli.py`, `src/agent_manager/prompt.py`, `src/agent_manager/results.py`, `src/agent_manager/harness/registry.py`, `src/agent_manager/runtime/engine.py`, `src/agent_manager/runtime/compile.py`, `src/agent_manager/runtime/checkpoint.py`, `src/agent_manager/orchestrate.py`, `src/agent_manager/steps/verify.py`, `src/agent_manager/steps/rollup.py`, `src/agent_manager/steps/reducers.py`, `src/agent_manager/steps/plan_check.py`, `src/agent_manager/steps/docs_commit.py`
- Test: none new; the whole suite re-runs.

**Interfaces:**
- Consumes: the module layout Task 3 produced (`runtime/walk.py`, `runtime/errors.py`).
- Produces: nothing new.

- [ ] **Step 1: Run the done-when grep to see what still names deleted code**

Run: `grep -rn "workflow.loader\|workflow.registry\|load_builtin\|from agent_manager import engine\|agent_manager.engine\|from_loader\|import yaml\|WorkflowLoadError" src tests`
Expected: FAIL (non-empty), at least `src/agent_manager/prompt.py` (`workflow/registry.py` in its module docstring), `src/agent_manager/results.py` (`workflow/loader.py`) and `src/agent_manager/harness/registry.py` (`workflow.registry.default_registry`).

- [ ] **Step 2: Rewrite the docstrings and comments**

Make each replacement exactly as written.

`src/agent_manager/cli.py`:
- `from `paths` via `store`, the phase walk from `engine`, and the dispatch from` → `from `paths` via `store`, the phase walk from `runtime.engine`, and the dispatch from`
- `    """The gate parameters `builtin/task.yaml` binds and `subtask_context` lacks.` → `    """The gate parameters `TASK`'s gates bind and `subtask_context` lacks.`

`src/agent_manager/prompt.py`:
- The two lines `lookup -- the same invariant `workflow/registry.py` enforces for `when:` and` / `gates at load time. A name the table does not carry is a document bug, reported` → `lookup. A name the table does not carry is a workflow bug, reported`
- `parameter names, as `engine.subtask_context` established. The two are not the` → `parameter names, as `walk.subtask_context` established. The two are not the`
- ``    `engine._bind_result` stores a phase's result in the context under the`` → ``    `walk._bind_result` stores a phase's result in the context under the``
- `    when there is not: `explore` is the first phase of `builtin/task.yaml` and` → `    when there is not: `explore` is the first agent phase of `TASK` and`
- The two lines `    empty list is the normal case -- no loop has happened, or the old engine` / `    is running and never supplies the key -- so this never goes through` → `    empty list is the normal case -- no loop has happened -- so this never goes through`
- `I3, `builtin/integrate.yaml`): the story tip being merged, inlined as a ref,` → `I3, `workflow.integrate.INTEGRATE`): the story tip being merged, inlined as a ref,`
- `` `engine.run_subtask(extra_context=...)`.`` → `` `runtime.engine.run_subtask(extra_context=...)`.``
- The three lines `describes. `cli.resume_start_phase` reads it, because an input resolved out of` / `another phase's result is a dependency on that phase having run in *this*` / `process -- the journal never replays the binding table.` → `describes. The pygents walk never needs it -- a checkpoint's pool carries every` / `earlier result -- but it stays the one place the mapping is written down.`

`src/agent_manager/results.py`:
- The module docstring (lines 1-16) becomes

```python
"""Result model names -> the pydantic model that validates one result file (§6 step 5).

A declared `phases.AgentPhase` carries its result model as the class itself.
This module is the table of those classes by name, and nothing more: no
validation happens here, and importing it reads no file.

`RESULT_MODELS` maps every result model a shipped workflow declares --
`ExploreResult`, `CriticResult`, `SpecResult`, `PlanResult`,
`ImplementResult`, `ReviewResult` from `workflow.task.TASK`, and
`ResolveResult` from `workflow.integrate.INTEGRATE` -- to itself by name.
`Verification` is not in it; no phase declares it, and it is reachable only as
`ExploreResult.verification`. A name with no entry still fails loudly at
dispatch time, which is strictly better than validating nothing and calling
the result `ok`.
"""
```

- `    """The `explore` phase's result file (`builtin/task.yaml` line 9)."""` → `    """The `explore` phase's result file."""`
- `    """The `critic` phase's result file (`builtin/task.yaml` lines 39 and 53)."""` → `    """The `validate_spec` and `validate_plan` phases' result file."""`
- `    """The `spec` phase's result file (`builtin/task.yaml`, the `spec` phase).` → `    """The `spec` phase's result file.`
- `agent says it wrote; `engine._document_paths` still derives `spec_path` from` → `agent says it wrote; `walk._document_paths` still derives `spec_path` from`
- `    """The `plan` phase's result file (`builtin/task.yaml` line 46).` → `    """The `plan` phase's result file.`
- `    """The `implement` phase's result file (`builtin/task.yaml` line 60).` → `    """The `implement` phase's result file.`
- `    """The `review` phase's result file (`builtin/task.yaml` line 66).` → `    """The `review` phase's result file.`
- `    """The `resolve` phase's result file (`builtin/integrate.yaml`, addendum I3).` → `    """The `resolve` phase's result file (addendum I3).`
- The `RESULT_MODELS` docstring

```python
"""Every `result:` name a builtin workflow declares, keyed by class name.

`builtin/task.yaml` declares the first six; `builtin/integrate.yaml` declares
`ResolveResult` for its `resolve` phase. `Verification` is absent on purpose: no
phase declares it, and it is reachable only as `ExploreResult.verification`.
"""
```

becomes

```python
"""Every result model a shipped workflow declares, keyed by class name.

`TASK` declares the first six; `INTEGRATE` declares `ResolveResult` for its
`resolve` phase. `Verification` is absent on purpose: no phase declares it, and
it is reachable only as `ExploreResult.verification`.
"""
```

`src/agent_manager/harness/registry.py`:
- The three lines `A fresh dict per call, for the reason `workflow.registry.default_registry`` / `gives: a module-level singleton is mutable global state any importer could` / `rebind an adapter in.` → `A fresh dict per call: a module-level singleton is mutable global state any` / `importer could rebind an adapter in.`

`src/agent_manager/runtime/engine.py`:
- `    # The binding, built and refused exactly as the old engine builds it:` / `    # before any agent exists, so a refusal records nothing.` → `    # The binding, built and refused before any agent exists, so a refusal` / `    # records nothing.`
- `        # A missing runner or an unresolvable input: a wiring or document bug` / `        # the old engine raises to its caller, `.phase`/`.parameter` intact.` → `        # A missing runner or an unresolvable input: a wiring or workflow bug` / `        # raised to the caller, `.phase`/`.parameter` intact.`

`src/agent_manager/runtime/compile.py`:
- `            # A wiring bug, not a phase failure: raised as the old engine raises` / `            # it, before anything runs, rather than escalated as a TypeError.` → `            # A wiring bug, not a phase failure: raised before anything runs,` / `            # rather than escalated as a TypeError.`
- `        # Outside the try, as in the old engine: an input no resolver provides` / `        # is a workflow bug and its `EngineError` must reach the caller as is.` → `        # Outside the try: an input no resolver provides is a workflow bug` / `        # and its `EngineError` must reach the caller as is.`
- `            # Total, as the old engine's agent branch is: an exception escaping` / `            # the walk would leave the subtask recorded `started` forever.` → `            # Total: an exception escaping the walk would leave the subtask` / `            # recorded `started` forever.`

`src/agent_manager/runtime/checkpoint.py`:
- `shift every stamp the old engine would have written (G10).` → `shift every phase-row stamp after it (G10).`

`src/agent_manager/orchestrate.py`:
- `    Best effort, like `mark_done` in `task.yaml`: a `BoardError` becomes a` → `    Best effort, like `TASK`'s `mark_done`: a `BoardError` becomes a`

`src/agent_manager/steps/verify.py`:
- ``    `engine.bind_arguments` (no workflow edit needed; the integrate workflow`` → ``    `walk.bind_arguments` (no workflow edit needed; the integrate workflow``

`src/agent_manager/steps/rollup.py`:
- The two lines ``the bare id string is `card` (`engine.py:98`, `bind_arguments` at`` / ``engine.py:163-212`). There is no `card_id` key, so a parameter by that name`` → ``the bare id string is `card` (`runtime.walk.subtask_context` and`` / `` `bind_arguments`). There is no `card_id` key, so a parameter by that name``
- `    Called by the two `best_effort: true` phases of `builtin/task.yaml`` → `    Called by the two `best_effort=True` steps of `TASK``
- `    document's `args`.` → `    workflow's `args`.`

`src/agent_manager/steps/reducers.py`:
- `# The `verify` phase's gate (`builtin/task.yaml`). `verify.run_suite` reports` → `# The `verify` phase's gate (`TASK`). `verify.run_suite` reports`
- `# The `implement` phase's gate (`builtin/task.yaml`), decision O7. The coder is` → `# The `implement` phase's gate (`TASK`), decision O7. The coder is`
- The two lines `# The `validate_spec` / `validate_plan` gate (`builtin/task.yaml` lines 40 and` / `# 54), ported from task.js lines 631-638 and 717-721. The critic is asked to` → `# The `validate_spec` / `validate_plan` gate (`TASK`), ported from task.js` / `# lines 631-638 and 717-721. The critic is asked to`
- `# The `review` phase's first gate (`builtin/task.yaml`), ported from task.js` → `# The `review` phase's first gate (`TASK`), ported from task.js`
- ``    ``result`` is the critic phase's own result: both ``engine._gate_values`` `` → ``    ``result`` is the critic phase's own result: both ``walk._gate_values`` ``
- ``    `engine.bind_arguments` binds strictly by parameter name out of a table of`` → ``    `walk.bind_arguments` binds strictly by parameter name out of a table of``
- ``    `review_hash`, and the yaml `args:` map holds literals, so neither could be`` → ``    `review_hash`, and a step's declared `args` hold literals, so neither could be``
- The three lines `    A module-level function rather than a closure built inside` / ``    `default_registry()`: `resolve(name) is resolve(name)` must hold across two`` / ``    registries, which is the same invariant `_PLACEHOLDERS` exists to preserve.`` → `    A module-level function rather than a closure: `TASK`'s digest names it by` / `    `module.qualname`, and one function object is what every workflow gating` / `    on it shares.`

`src/agent_manager/steps/plan_check.py`:
- ``    `engine._run_deterministic`, which is total, records the phase failed and`` → ``    `walk.run_one_step`, which is total, records the phase failed and``

`src/agent_manager/steps/docs_commit.py`:
- ``    `engine.subtask_context` binds `card_details` to `None` when the caller`` → ``    `walk.subtask_context` binds `card_details` to `None` when the caller``

- [ ] **Step 3: Run the done-when grep to see it pass**

Run: `grep -rn "workflow.loader\|workflow.registry\|load_builtin\|from agent_manager import engine\|agent_manager.engine\|from_loader\|import yaml\|WorkflowLoadError" src tests`
Expected: no output, exit status 1.

Also run `grep -rn "engine\._\|engine\.py\|builtin/\|--engine" src` and expect no output.

- [ ] **Step 4: Check the CLI surface**

Run: `uv run am run --help | grep -c -- '--engine'; uv run am resume --help | grep -c -- '--engine'`
Expected: `0` twice.

Run: `uv run am run --card 7a744199-3662-4753-9b47-fcc2a228410c --branch-prefix m6 --engine pygents; echo "exit=$?"`
Expected: a Typer usage error containing `No such option: --engine` on stderr, nothing on stdout, and `exit=2`.

- [ ] **Step 5: Check rule 1 for the modules below the runtime**

Run:

```bash
uv run python -c "import sys; import agent_manager.dispatch, agent_manager.prompt, agent_manager.results, agent_manager.runtime.walk, agent_manager.runtime.errors, agent_manager.workflow.phases; loaded = sorted(m for m in sys.modules if m == 'pygents' or m.startswith('pygents.')); assert not loaded, loaded; print('rule 1 ok')"
```

Expected: `rule 1 ok`.

Run: `grep -rln "^from pygents\|^import pygents" src/agent_manager`
Expected: only files under `src/agent_manager/runtime/`, and neither `runtime/walk.py` nor `runtime/errors.py`.

- [ ] **Step 6: Run the whole suite**

Run: `uv run pytest`
Expected: PASS, `tests/e2e` included (the `e2e`-marked opt-in modules deselected by `addopts`).

- [ ] **Step 7: Commit**

```bash
git add src/agent_manager
git commit -m "Point docstrings at the code that still exists after the YAML engine's removal"
```

- [ ] **Step 8: Hand-off note**

In the final report (not a file), state: the Review Focus items 1, 2 and 5 are not pinned by any test after this card (the spec forbids new tests); the `test_builtin_integrate` coverage list from Task 3 Step 1n; and the Deviations section's list, so the reviewer can check each one against the spec.
