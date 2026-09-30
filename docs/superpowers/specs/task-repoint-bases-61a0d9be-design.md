# Repoint bases/integration/orchestrate at runs.py and delete cli.py's deferred imports (61a0d9be)

Card: 61a0d9be-083c-4627-9d0f-6a817f36553d. Story: 4bc0a3e0 ("cli.py sheds its collaborator role"). Milestone: 9c44c2fb (milestone 13, architecture cleanup).
Spec of record: `docs/superpowers/specs/2026-09-30-architecture-cleanup-design.md`, decision S1 (§3), testing note (§6), out-of-scope list (§8).
Base branch: `m13/task-move-dry-run-payload-s-64babfa2` (tip `7256dd6`), not master. Master has no `runs.py`.

This is the last of four S1 subtasks. Siblings 46244d0e, 1c21b3dd and 64babfa2 are done and already moved the helpers into `runs.py`. This card only repoints the callers and removes the scaffolding those moves left behind.

## Scope

Line numbers below were read on the base branch. They will drift, so re-read the code as built. The card's own line numbers (895/1137/1460) and its mention of a deferred `integration` import are stale and should be ignored.

1. **`bases.py`.** Replace `from agent_manager import cli, ...` (about line 34) with an import of `runs`. Rewrite every `cli.RunnerFactory`, `cli.gate_context` and `cli.worktree_for` as `runs.<name>`. After this, `bases.py` does not import `cli` at all.
2. **`integration.py`.** Same change at about line 29, for the same three names. After this, `integration.py` does not import `cli` at all.
3. **`orchestrate.py`.** Add `runs` to the import line (about line 55). Keep `cli` in that line.
   - Repoint only the names that S1 moved: `RunnerFactory`, `UnknownRunError`, `NotResumableError`, `CheckpointMismatchError`, `mint_run_id`, `orphan_attempts`, `worktree_for`, `continuable_checkpoint` and `resolve_repo_dir`. Apply this to annotations and docstring mentions as well as calls.
   - These stay as `cli.`, because they are still defined in `cli.py` and are not part of S1:
     - `cli.default_runner_factory` (the explicit exemption, used at about lines 800/805/1491)
     - `cli.SubtaskDrive`
     - `cli.drive_subtask_async`
     - `cli._utcnow`
     - `cli.resume_run`
     - `cli.checkpoint_resume_phase`
   - `orchestrate.py` therefore still legitimately imports `cli`.
4. **`cli.py` deferred imports.** Delete the in-function `from agent_manager import orchestrate` imports and their cycle-breaking comments (currently two sites, at about lines 999 and 1366). Replace them with one module-level `orchestrate` import.
   - This is safe only if no cycle is left. Once steps 1–3 are done, `bases` and `integration` no longer import `cli`. `orchestrate` still imports `cli`, though, so a module-level `cli → orchestrate → cli` cycle would remain.
   - If a top-level import fails for that reason, keep the deferred import at those call sites. Record the reason in the card's notes. Do not restructure `orchestrate`, because moving `default_runner_factory` or the drive functions is out of scope.
   - There is no deferred `integration` import left in `cli.py`, so there is nothing to remove for it.
5. **`cli.py` re-exports.** In the `from agent_manager.runs import (...)` block (about lines 49–69), drop each name that no module outside `cli.py` still reads through `cli.`. "Outside" includes `tests/`.
   - Names still used inside `cli.py` stay imported, but the "re-exported so every `cli.X` caller keeps working" comment must be reworded or removed to match what the block now does.
   - Many names (`worktree_for`, `resolve_repo_dir`, `mint_run_id`, `select_resumable`, `orphan_attempts`, `gate_context`, `continuable_checkpoint`, the error types) are still read as `cli.X` by `tests/test_cli.py`, `tests/test_integration.py`, `tests/workflow/test_task.py` and `tests/e2e/*`. Those stay. Existing tests are not rewritten to drop them. In particular, `tests/test_cli.py:4337` calls `cli.continuable_checkpoint(...)` directly (not a monkeypatch) — dropping it from the re-export block breaks that test.
   - `RUN_ID_TIME_FORMAT`, `WORKTREE_PARTS` and `DryRunPlan` are read nowhere as `cli.X` and are not used bare inside `cli.py` either; drop them. `RunnerFactory` is read nowhere as `cli.X` in live code — the `cli.RunnerFactory` occurrences in `tests/test_bases.py`, `tests/test_integration.py` and `tests/test_cli.py` are docstring prose, not attribute access — so it drops too, even though it is one of the names S1 moved. `compute_dry_run_plan` is read nowhere as `cli.X` but stays, because `cli.py`'s own `dry_run_payload` still calls it by its bare name.
   - Do not touch `default_runner_factory`. It was never re-exported and stays defined in `cli.py`.

## Observable behaviour

No change to the CLI or the runtime (spec §5).

- Every `am` command produces the same JSON envelope (`{"ok": true, "data": ...}`), the same `--pretty` output and the same exit codes.
- Error paths are unchanged. `UnknownRunError`, `NotResumableError`, `CheckpointMismatchError`, `CliError` and `RepoDirError` are the same objects (`cli.X is runs.X` still holds for every name that is still re-exported). They are raised and rendered exactly as before.
- The only visible difference is which module owns each attribute lookup. That matters for monkeypatching (see the next section).

## Test-suite consequence of repointing (monkeypatch targets)

`tests/test_orchestrate.py` monkeypatches `cli.continuable_checkpoint` in two places: around line 2948 (the `broken` fixture) and around line 3927 (`_never_consulted`).

After step 3, `orchestrate` looks up `runs.continuable_checkpoint`, so a patch on `cli` no longer reaches it:

- The first test would fail.
- The second would pass without testing anything.

Both `setattr` targets, and the `_never_consulted` failure message that names `cli.continuable_checkpoint`, must be retargeted to `runs`. This counts as repointing imports at `runs`, which is what spec §6 allows. It is the only permitted edit to existing tests.

Before finishing, grep `tests/` for `setattr(cli, "<moved name>"` and for string-path patches (`"agent_manager.cli.<moved name>"`) of any other moved name that `bases`, `integration` or `orchestrate` now read from `runs`. Retarget those the same way.

## Tests

The placement rule is CLAUDE.md's "tests mirror `src/agent_manager/` under `tests/`": one flat `tests/test_<module>.py` per source module. There is no unit/integration tier split for these modules. The precedent is the ast-based import checks already in `tests/test_runs.py:98-109`, `tests/test_control.py`, `tests/test_census.py` and `tests/test_orchestrate.py`.

New tests:

- **`tests/test_bases.py`: `test_bases_source_never_imports_cli`.** Module-mirrored file for `bases.py`. Parse `bases.py` with `ast`, collect every `Import`/`ImportFrom` at any depth, and assert that none of them binds `agent_manager.cli`. That means no `from agent_manager import cli`, no `import agent_manager.cli` and no `from agent_manager.cli import ...`. This is a static check, not a behavioural one.
- **`tests/test_integration.py`: `test_integration_source_never_imports_cli`.** Module-mirrored file for `integration.py`. Same ast assertion against `integration.py`.
- There is deliberately no equivalent test for `orchestrate.py`, which still needs `cli` for `default_runner_factory` and the drive functions.

Existing tests:

- `tests/test_cli.py`, `tests/test_orchestrate.py`, `tests/test_bases.py`, `tests/test_integration.py`, `tests/test_runs.py`, `tests/workflow/`, `tests/e2e/` must all pass.
- The only edit to existing tests is the monkeypatch retargeting described above.

Verification is `uv run pytest`, the full suite. There is no lint or typecheck command (CLAUDE.md).

## Out of scope

- Anything in spec §8: the pygents turn/phase model, the checkpoint format, the harness adapter contract, adding a typecheck/CI gate, rewriting the roughly 261 legitimate boundary monkeypatches, and replacing grafo.
- Moving `default_runner_factory`, `SubtaskDrive`, `drive_subtask_async`, `resume_run` or `checkpoint_resume_phase` out of `cli.py`.
- Moving any further helpers into `runs.py`, or changing `runs.py`'s contents.
- Other milestone-13 cards: schema, gate evaluation, resolver unification, StoryRecorder, error hierarchy, runtime typing, `paths.py`, Collaborators injection.
