# Move the simple cli.py helpers into runs.py — subtask design (46244d0e)

Parent story: 4bc0a3e0 "cli.py sheds its collaborator role" (milestone 9c44c2fb). Spec of record: `docs/superpowers/specs/2026-09-30-architecture-cleanup-design.md`, decision S1 and its "`default_runner_factory` exemption" paragraph. This document narrows S1 to its first step; it adds no new design.

## Scope

Create `src/agent_manager/runs.py`, a plain module with no `typer` import, and move these five definitions into it verbatim (same signatures, bodies, and docstrings):

- `resolve_repo_dir` (cli.py:166)
- `mint_run_id` (cli.py:181)
- `worktree_for` (cli.py:191)
- `RunnerFactory` Protocol (cli.py:618)
- `gate_context` (cli.py:660)

Required supporting definitions. The five names reference module-level definitions in `cli.py`. `runs.py` must not import `cli`: `cli` will import `runs` at top level, so a `runs -> cli` import would be circular, and S1 says nothing downstream of `runs` imports `cli`. So the definitions they depend on move with them, also verbatim:

- `RUN_ID_TIME_FORMAT` (cli.py:55), used by `mint_run_id`
- `WORKTREE_PARTS` (cli.py:58), used by `worktree_for`
- `CliError` (cli.py:63) and `RepoDirError` (cli.py:67), raised by `resolve_repo_dir`. `CliError` has to come too because `RepoDirError` subclasses it, and sibling 1c21b3dd will need it in `runs.py` for the run-error types it moves there.

`runs.py` imports only what those bodies need (`dag`, `Path`, `datetime`, `Sequence`, `Any`, `Protocol`, `Store`, `AgentPhaseRunner`) and does not import `cli`, `typer`, or `harness.launcher`.

In `cli.py`, delete the moved definitions and replace them with a single `from agent_manager.runs import ...` that re-exports every moved name: the five helpers plus the four supporting names. After this, `cli.X is runs.X` for every moved `X`. That keeps these working unchanged:

- attribute access such as `cli.worktree_for(...)`, `cli.RepoDirError`, and `cli.CliError`
- the `HANDLED` tuple
- the remaining `CliError` subclasses defined in `cli.py`
- `except CliError`
- bare-name calls inside `cli.py`

`default_runner_factory` (cli.py:637) stays in `cli.py` unchanged. Its body resolves the bare name `run_direct` (imported at cli.py:44) from `cli`'s globals at call time. `tests/e2e/test_live_control.py:179`, `tests/e2e/test_milestone_run.py:294,421` and `tests/e2e/test_milestone_resume.py:241` intercept it with `monkeypatch.setattr(cli, "run_direct", ...)`. The `run_direct` import at cli.py:44 also stays.

## Out of scope

- Repointing any caller: `bases.py`, `integration.py`, `orchestrate.py` and all tests keep importing `cli`. That is 61a0d9be's job.
- Deleting the deferred imports at cli.py:895,1137,1460, dropping re-exports, or adding the import-graph test (also 61a0d9be).
- Moving `orphan_attempts`, `continuable_checkpoint`, `select_resumable`, `UnknownRunError`, `NotResumableError` or `CheckpointMismatchError` (that is 1c21b3dd). Moving any other `CliError` subclass.
- Touching `dry_run_payload` (that is 64babfa2).
- The S7 error-hierarchy merge into `errors.py`.
- From both cards: pygents' turn/phase model, checkpoint format, the harness adapter contract, adding a typecheck/CI gate, rewriting the ~261 existing monkeypatch calls, and replacing grafo.

## Observable behavior and error paths

Nothing observable changes. Every CLI command gives the same JSON/`--pretty` envelopes, exit codes and run ids as before. `resolve_repo_dir` still raises `RepoDirError` with the same message for a non-directory. `isinstance`/`issubclass` checks against `cli.CliError`/`cli.RepoDirError` still hold because the classes are the same objects. The only side effect of importing `runs` is that its own definitions exist.

## Tests

No test file changes. The acceptance check is `uv run pytest` green on the unmodified suite. These existing tests are the evidence:

- **Unit tier** (`tests/test_cli.py`, e.g. :117 `pytest.raises(cli.RepoDirError)`, :389 `issubclass(cli.NotResumableError, cli.CliError)`; `tests/workflow/test_task.py:429` `cli.gate_context`). These are pure-function and re-export checks.
- **Step/engine tier with temp git repos and a temp board** (`tests/test_orchestrate.py`, `tests/test_bases.py`, `tests/test_integration.py`, which call `cli.worktree_for`, `cli.mint_run_id`, `cli.resolve_repo_dir` and `cli.RunnerFactory`). These show the re-exports behave identically.
- **Default-suite e2e tier, fake harness** (`tests/e2e/test_live_control.py:179`, `tests/e2e/test_milestone_run.py:294,421`, `tests/e2e/test_milestone_resume.py:241`). These show `default_runner_factory` still picks up the patched `cli.run_direct`.
- The opt-in real-harness e2e tests (`tests/e2e/test_real_harness_*.py`) are outside the default suite and are not part of acceptance.

No new test is required. Under design spec §14, a new test would be a pure-function unit test placed as `tests/test_runs.py`, not an e2e test. Asserting `cli.<name> is runs.<name>` there is optional, and the import-graph test is 61a0d9be's, not this card's.
