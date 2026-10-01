# Move resume helpers and error types into runs.py (subtask 1c21b3dd)

Parent story: 4bc0a3e0 "cli.py sheds its collaborator role". Governing decision: S1 in `docs/superpowers/specs/2026-09-30-architecture-cleanup-design.md`. This subtask narrows S1 to one more batch of names and follows the re-export pattern established by the previous subtask 46244d0e exactly.

## Starting point

Work starts from the state of branch `m13/task-move-the-simple-cli-py-46244d0e` (done), not `master`: `src/agent_manager/runs.py` exists there, holds `RUN_ID_TIME_FORMAT`, `WORKTREE_PARTS`, `CliError`, `RepoDirError`, `resolve_repo_dir`, `mint_run_id`, `worktree_for`, `RunnerFactory`, `gate_context`, and `cli.py` re-exports them through `from agent_manager.runs import (...)`. Line numbers below are from that branch and drift; re-read before editing.

## Scope

Move these definitions verbatim (bodies, docstrings, signatures unchanged) from `src/agent_manager/cli.py` into `src/agent_manager/runs.py`:

- `UnknownRunError(CliError)` (cli.py ~70)
- `NotResumableError(CliError)` (cli.py ~120)
- `CheckpointMismatchError(CliError, runtime_engine.CheckpointMismatch)` (cli.py ~130) — the multiple inheritance stays exactly as-is; reworking it is a later decision (S7), not this one.
- `select_resumable` (cli.py ~434)
- `orphan_attempts` (cli.py ~482)
- `continuable_checkpoint` (cli.py ~545)

`runs.py` gains only the imports these need: `models`, `store as store_module`, `workflow.task as task_workflow`, `runtime.engine as runtime_engine` (plus anything else the moved bodies actually reference). Update the `runs.py` module docstring only as far as needed to cover resume helpers.

`cli.py` adds the six names to its existing named `from agent_manager.runs import (...)` block. It must stay a named import, not `from agent_manager import runs`, because `cli.py` defines its own `runs` Typer command. `cli.py` drops any of its own imports that become unused after the move, and keeps those still used elsewhere.

Explicitly not in scope:

- `checkpoint_resume_phase` (cli.py ~501), although it sits between the moved functions, stays in `cli.py`.
- The other `CliError` subclasses in `cli.py` (`ParentlessCardError`, `UnknownCardError`, `UnknownPhaseError`, `UnknownAttemptError`, `NotRunningError`, …) stay.
- `dry_run_payload` is sibling 64babfa2's.
- Repointing `bases.py`, `integration.py`, `orchestrate.py` at `runs`, deleting `cli.py`'s deferred imports, dropping the re-exports, and the static import-graph test for `bases`/`integration` are sibling 61a0d9be's. `orchestrate.py`'s `cli.UnknownRunError` / `cli.NotResumableError` / `cli.CheckpointMismatchError` / `cli.orphan_attempts` / `cli.continuable_checkpoint` call sites are left untouched.
- Spec §8 exclusions: no change to the turn/phase model, the checkpoint format, or the harness adapter contract. No typecheck/CI gate. No rewriting of the existing monkeypatch calls. No grafo changes.

## Observable behavior

None changes. `cli.X is runs.X` for every moved name, so every `cli.X` caller, `isinstance`/`except` site, and monkeypatch target keeps working. The error envelope is `{"ok": false, "error": {"type": type(error).__name__, ...}}` (cli.py ~172). It uses only `__name__`, so the envelope `type` strings (`UnknownRunError`, `NotResumableError`, `CheckpointMismatchError`) and the exit code 3 via `HANDLED` stay the same. Only `__module__` changes, to `agent_manager.runs`. `CheckpointMismatchError` is still a `runtime_engine.CheckpointMismatch`.

## Error paths

This subtask adds none. The constraints that must hold after the move are these. `runs.py` imports no `typer`, no `agent_manager.cli`, and no `agent_manager.harness.launcher`, and importing it loads neither `typer` nor `agent_manager.cli`. The existing tests `test_runs_source_imports_neither_typer_nor_cli_nor_launcher` and `test_importing_runs_loads_neither_typer_nor_cli` enforce this. The new imports (`runtime.engine`, `workflow.task`, `store`, `models`) must not transitively pull in `cli` or `typer`, or those tests fail.

## Tests

Test-placement rule: tests mirror the source module flat under `tests/`. The only tier split is the `e2e` marker (slow, real harness, excluded by default). All tests below are default-suite, non-e2e.

- `tests/test_runs.py` (default suite, mirrors `runs.py`): extend `MOVED_NAMES` with `UnknownRunError`, `NotResumableError`, `CheckpointMismatchError`, `select_resumable`, `orphan_attempts`, `continuable_checkpoint`, so that the parametrized `test_cli_re_exports_the_moved_name_as_the_same_object` pins `cli.X is runs.X` for each.
- `tests/test_runs.py` (default suite): add a test that the three error types are defined in `runs` (`__module__ == "agent_manager.runs"`), subclass `runs.CliError`, and that `runs.CheckpointMismatchError` is a subclass of `runtime_engine.CheckpointMismatch`.
- `tests/test_runs.py` (default suite): add a test that `checkpoint_resume_phase` stays in `cli` (`cli.checkpoint_resume_phase.__module__ == "agent_manager.cli"` and `not hasattr(runs, "checkpoint_resume_phase")`), mirroring `test_default_runner_factory_stays_in_cli`.
- Existing tests in `tests/test_runs.py` (default suite): `test_runs_source_imports_neither_typer_nor_cli_nor_launcher` and `test_importing_runs_loads_neither_typer_nor_cli` stay unchanged and must still pass with the new imports.
- Existing tests in `tests/test_cli.py` (default suite): these are the regression oracle and stay unchanged. They include `test_select_resumable_*`, `test_orphan_attempts_are_exactly_the_ones_recorded_started`, `test_continuable_checkpoint_is_the_open_matching_row_or_none`, and the envelope-type assertions for the three errors. They call through `cli.X`, and spec §6 requires no edits to them.

Done when `uv run pytest` is green.
