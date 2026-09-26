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
