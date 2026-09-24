# Extract the shared subtask driver from `run_card` (card 38809280)

Parent story: f8290fd6 "Run a milestone: rollup, the shared driver, the runner". Source decision: addendum O4 in `docs/superpowers/specs/2026-09-24-orchestration-design.md` (lines 65-69). This is a pure refactor. It narrows O4 to one function in `src/agent_manager/cli.py` plus one test.

## Scope

Move the per-subtask half of `run_card` (currently `cli.py:660-768`) into a new public function in `cli.py`. This spec calls it `drive_subtask`. `run_card` then calls it. Later cards reuse it: c9037ac9 (`orchestrate.run_milestone`) calls it once per subtask against one shared store and run id.

The driver does four things, in the order `run_card` does them today:

1. Load the workflow once per call with `load_builtin(WORKFLOW_NAME)`.
2. Build the runner with `runner_factory` (`default_runner_factory` when it is `None`), passing `workflow`, `store`, `run_id`, `story_id=parent.id` and `card_id=card.id`.
3. Call `engine.run_subtask(workflow, store, story_id=parent.id, subtask=subtask, repo_dir=repo_dir, commands=commands, card=card, parent_story=parent, extra_context=gate_context(commands, allow_no_verification), agent_runner=runner)`.
4. Collect the warnings as `list(summary.warnings) + list(getattr(runner, "warnings", []))`.

Signature (keyword-only, everything passed in, no module-level mutable state):

```python
def drive_subtask(
    *,
    store: Store,
    run_id: str,
    card: models.Card,        # the subtask's board card
    parent: models.Card,      # the story's board card; parent.id is the story id
    subtask: models.SubtaskRun,
    repo_dir: Path,           # already resolved
    commands: Sequence[str] = (),
    allow_no_verification: bool = False,
    runner_factory: RunnerFactory | None = None,
) -> SubtaskDrive
```

`SubtaskDrive` is a small frozen dataclass. It is internal state, so it is a dataclass and not a pydantic model, per CLAUDE.md. It has two fields: `summary: engine.SubtaskSummary` and `warnings: list[str]`. `warnings` is the combined list from step 4.

## What the driver does NOT do (stays in `run_card`, byte-for-byte)

- Resolving `repo_dir`, both `board.show` reads, `ParentlessCardError`, the branch and worktree derivation, `clock()`, and `mint_run_id`. The board reads stay before a run id exists, so a bad card still leaves no run directory.
- `Store.open`, and `store.close()` in `finally`. The driver never opens or closes the store.
- Building and recording the `Run`, `StoryRun` and `SubtaskRun` rows. These are still written before the walk starts.
- The final `record_run`/`record_story`/`record_subtask` with `summary.status`, and the returned dict. The dict keys, values and order stay unchanged. `warnings` now comes from `SubtaskDrive.warnings`.
- The Typer command's `HANDLED` handling and the exit codes `EXIT_ERROR`/`EXIT_ESCALATED`. These are untouched.

One order change is unavoidable. `load_builtin` now runs after `Store.open` and after the rows are written, not before. It can only fail if the package itself is broken, so no test or observable path changes. `run_card` no longer loads the workflow itself.

## Error paths

The driver catches nothing. An exception from the factory, `gate_context`, or `engine.run_subtask` propagates exactly as it does today. It passes through `run_card`'s `finally` (the store is closed) and then to the command's existing handlers. An escalation is not an exception: it comes back as `summary.status == "escalated"`, as it does today.

## Out of scope

Rollup (bf26f482), `orchestrate.run_milestone` (c9037ac9), `am run --milestone` and its e2e tests (3e0ab2b9). Also everything in addendum section 4: parallel stories, Integrate, milestone-aware resume, watch/retry/cancel, git-measured review counts, verification discovery. Do not add a stacked-base or multi-story concept to the driver.

## Tests

No existing test is modified. The existing suite is the judge of the refactor: the `run_card` tests in `tests/test_cli.py`, `tests/e2e/test_production_wiring.py`, and the whole default `uv run pytest`, including `tests/e2e`.

One new test:

- `test_drive_subtask_drives_two_subtasks_under_one_store_and_run` is **Engine tier**, so it goes in `tests/test_cli.py`. Design spec section 14 puts `run_card`-level code at Engine tier: a fake `AgentPhaseRunner` on Steps-tier fixtures (temp git repo + temp brd board, no network). It is not e2e and not a pure unit test. It is marked `@requires_git @requires_brd` and uses the `project` fixture.
  - It builds a milestone, then a story, then two subtask cards with `_add_card`, and reads them back with `board.show`.
  - It mints one run id. It opens one `Store` and records the `Run` and `StoryRun` rows. For each subtask it derives the branch (`dag.task_branch`) and worktree (`worktree_for`), records a `started` `SubtaskRun`, and calls `cli.drive_subtask(..., runner_factory=lambda **kwargs: fake_runner())`.
  - It closes the store in `finally`.
  - It asserts that both results have `summary.status == "done"` and `warnings == []`. It also asserts that `store.load_run(run_id)` shows the one story holding both subtasks, each with status `done`.
  - It reuses the existing `fake_runner`. The fake knows nothing beyond the brief. It writes only the canned spec and plan stand-ins, never computes the plan hash, never commits, and learns no result path except through the prompt context.

## Verification

- Full suite: `uv run pytest`
- Typecheck: none
- Lint: none
