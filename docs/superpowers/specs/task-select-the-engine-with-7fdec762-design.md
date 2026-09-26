# Select the engine with `--engine` through run, milestone and Integrate (card 7fdec762)

Narrows plan Task 4.4 of `docs/superpowers/plans/2026-09-25-pygents-engine.md`. Source of truth: `docs/superpowers/specs/2026-09-25-pygents-engine-design.md` (extending the agent-manager, orchestration and integrate designs). Story a6c7bff3 "Checkpoints and resume"; blocked_by 5698e4f6 (done), whose `runtime.engine.run_subtask(..., resume_from=...)` this card calls as-is.

Note: the exploration findings handed to this stage were truncated at 8000 characters, mid-sentence in the test-placement rule. The rule below was re-read from the design spec §14 and `pyproject.toml` directly, not reconstructed from the cut-off text.

## Scope

- `cli.py`: add `Engine = Literal["yaml", "pygents"]`. `drive_subtask(..., engine: Engine = "yaml")`, `run_card(..., engine: Engine = "yaml")`, and the Typer `run` command gets `--engine` (default `yaml`), forwarded to `run_card` or `orchestrate.run_milestone`.
- `orchestrate.py`: `run_milestone(..., engine: Engine = "yaml")`. It passes `engine` to every driver call through `run_story_lane`, and to `integration.integrate_milestone`. The `Driver` Protocol gains the same `engine` keyword, because it mirrors `drive_subtask`'s signature.
- `integration.py`: `integrate_milestone(..., engine="yaml")` passes it to `_resolve_conflict(..., engine)`.
- Dispatch inside `drive_subtask`:
  - `engine == "yaml"`: exactly today's code. It calls `agent_manager.engine.run_subtask` with the YAML `Workflow` from `load_builtin("task")`.
  - `engine == "pygents"`: calls `agent_manager.runtime.engine.run_subtask(workflow.task.TASK, store, ...)` with the same keyword arguments the yaml call passes (`story_id`, `subtask`, `repo_dir`, `commands`, `card`, `parent_story`, `extra_context=gate_context(...)`, `agent_runner`, `should_stop`), minus `start_phase`. It does not pass `resume_from`.
- The same split applies in `_resolve_conflict`. On pygents it calls `runtime.engine.run_subtask(workflow.integrate.INTEGRATE, ...)` with the same `extra_context` (`merge_tip`, `conflict_files`, gate context). It does not pass `card` or `parent_story`, matching today's call.
- `RunnerFactory` still gets `workflow=` on both engines. That value is always the YAML `Workflow` that `load_builtin` returns (`task`, or `integrate` for the resolver), never `TASK` or `INTEGRATE`. `dispatch.AgentRunner` reads it only for by-name gates, and the phase model never produces those. The `RunnerFactory` signature does not change.
- The fail-fast `load_builtin` preflights in `run_card` and `run_milestone` stay as they are on both engines.
- `SubtaskDrive`'s warnings merge (summary warnings plus the runner's out-of-band warnings) is the same on both engines.

## Invariants

- RULE 1: `cli.py`, `orchestrate.py` and `integration.py` must not import `pygents`. They reach it only through `agent_manager.runtime.engine`. Where that import sits (module top or lazy inside the pygents branch) is the plan's choice. `workflow/phases.py`, `task.py` and `integrate.py` stay pygents-free.
- Name collision: `cli.py` and `integration.py` already use the old engine module under the name `engine` (`engine.run_subtask`, `engine.SubtaskSummary`, `engine.AgentPhaseRunner`). A parameter called `engine` would shadow that module inside `drive_subtask` and `_resolve_conflict`. Inside those bodies the module must be reached under a name the parameter cannot shadow. The public keyword stays `engine`, as the plan names it.
- RULE 5 / G10: the JSON `data` of `run --card` and `run --milestone`, plus the journal lines, phase/attempt rows, `SubtaskSummary` and escalation payloads, must be the same on both engines, apart from `run_id` and timestamps.
- The parallel milestone works on pygents unchanged. Each lane thread's call to `runtime.engine.run_subtask` does its own `asyncio.run`. This card adds no event loop, no lane-as-task and no supervisor.
- RULE 3: this card adds no code in `runtime/`.

## Observable behavior

- `am run --card X` and `am run --milestone M`, with no `--engine` or with `--engine yaml`, behave byte-for-byte as they do today.
- With `--engine pygents`, the same commands give the same envelope `{"ok": true, "data": ...}` (and the same `--pretty` rendering). The only differences are in `run_id` and timestamps.
- `--dry-run` accepts `--engine` and ignores it. It drives nothing.
- A relaunch with `--engine pygents` re-drives a non-done subtask from its first phase, exactly as yaml does. Continuing from a checkpoint is card 02890d5d's job, not this card's.

## Error paths

- `--engine bogus` (any value outside `yaml`/`pygents`) is a usage error: exit 2, nothing on stdout, and no run directory is created. Typer's own choice validation for the `Literal` annotation should produce this. If Typer 0.27.2 does not validate a `Literal` cleanly, fall back to the existing `typer.BadParameter` pattern (cli.py ~1001-1037), which also exits 2. Do not add a new validation helper beyond that.
- Neither engine adds new exceptions. An escalation is still `summary.status == "escalated"`, and a stop is still `"stopped"`. `CheckpointMismatch` cannot be raised here, because `resume_from` is never passed.

## Out of scope

`am resume --engine`, checkpoint continuation on relaunch, orphan-attempt marking and digest-mismatch exit 3 (all card 02890d5d). The second `engine.run_subtask` call inside `resume_run` (cli.py ~1382) is not touched. Also out: the supervisor tree, exactly-once phases, prompt benchmarking, upstream pygents fixes, and any change to `runtime/engine.py`'s resume logic.

## Tests

Test-placement rule: design spec §14 plus `pyproject.toml`. Pure logic and CLI plumbing go in unit tests under `tests/`, mirroring `src/`, with the launcher/runner injected and no harness run. Fake-`claude` end-to-end tests live in `tests/e2e/`, unmarked, in the default suite. Only real-harness tests carry the `e2e` marker, and those are excluded by `addopts`. This card adds nothing to the real-harness tier.

1. Unit tier, `tests/test_cli.py`: `run --card X --engine bogus` exits 2, stdout is empty, and no run directory exists.
2. Unit tier, `tests/test_cli.py`: `drive_subtask(engine="pygents")`, with `runtime.engine.run_subtask` stubbed, gets `workflow.task.TASK` as its first argument. Its keywords are the yaml call's keywords minus `start_phase`, with no `resume_from`. The factory gets the YAML `Workflow` from `load_builtin`. `drive_subtask()` with the default engine still calls the old `engine.run_subtask`.
3. Unit tier, `tests/test_orchestrate.py`: `run_milestone(engine="pygents")`, with a fake `driver`, passes `engine="pygents"` to every driver call and to `integrate_milestone`. With no argument, `"yaml"` is passed.
4. Unit tier, `tests/test_integration.py`: `_resolve_conflict(engine="pygents")`, with `runtime.engine.run_subtask` stubbed, gets `workflow.integrate.INTEGRATE` and the same `extra_context`. The runner factory gets the YAML `integrate` `Workflow`. The plan lists no parametrized e2e case for the resolver path, so this unit test is what covers it. Parametrizing `tests/e2e/test_integrate.py` is not required by this card.
5. Fake-claude e2e tier, `tests/e2e/test_production_wiring.py`: parametrize over `engine ∈ {yaml, pygents}` by passing `--engine`. The existing assertions hold on both engines. The JSON `data` is the same on both, ignoring `run_id` and timestamps.
6. Fake-claude e2e tier, `tests/e2e/test_milestone_run.py`: the same parametrization and the same identical-`data` assertion, including the review-failure-then-relaunch scenario. On pygents that relaunch re-drives from the start.
7. Fake-claude e2e tier, `tests/e2e/test_parallel_milestone.py`: the same parametrization. Lanes overlap, a single lane runs sequentially, the journal is contiguous and rebuilds the projection, the escalation stops the other lane, and the relaunch finishes. All of this must hold on pygents with one `asyncio.run` per lane thread.
8. Each module keeps its `test_this_module_runs_in_the_default_suite_unmarked` guard. Parametrization must not add the `e2e` marker.

Fake `claude` stays brief-driven: no engine-awareness is added to `tests/e2e/fake_claude.py`.

Verify: `uv run pytest` is green, with the whole default suite passing on both engines, parallel milestone included. Commit: `feat(cli): --engine selects the YAML or the pygents engine`.
