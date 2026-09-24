<!-- task-pipeline: validated -->
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

---

# Extract the shared subtask driver Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `cli.drive_subtask`, which drives one subtask against a store and run id the caller already holds. Then make `run_card` call it, and leave `run_card`'s observable behaviour exactly as it is.

**Architecture:** The new keyword-only function `drive_subtask` and the frozen dataclass `SubtaskDrive` live in `src/agent_manager/cli.py`, directly after `gate_context`. The driver loads the workflow, builds the runner through the factory, calls `engine.run_subtask` with `gate_context(...)` as `extra_context`, and returns the summary plus the combined warnings. `run_card` keeps its board reads, run-id minting, store lifecycle, row writes, final records and return dict. Only the four driving lines move out.

**Tech Stack:** Python, Typer, Pydantic, stdlib `dataclasses`, pytest, run with `uv run pytest`.

**Spec:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m3/task-extract-the-shared-38809280/docs/superpowers/specs/task-extract-the-shared-38809280-design.md` (prepended above, verbatim).

**Worktree / branch:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m3/task-extract-the-shared-38809280`, branch `m3/task-extract-the-shared-38809280`. The branch was cut from `m3/task-add-am-run-milestone-36faf21e`. No sibling card's code (rollup, `orchestrate.py`, `--milestone`) exists here, and none is assumed. Every path below is relative to this worktree.

## Global Constraints

- Pure refactor: no existing test is modified, and the whole default `uv run pytest` must stay green, including `tests/e2e`.
- The driver is keyword-only, takes everything as arguments, holds no module-level mutable state, and catches no exceptions.
- `SubtaskDrive` is a frozen `dataclasses.dataclass`, not a pydantic model (CLAUDE.md: dataclasses for internal-only state). Its fields are `summary: engine.SubtaskSummary` and `warnings: list[str]`.
- The warnings are `list(summary.warnings) + list(getattr(runner, "warnings", []))`, in that order.
- In `run_card`, both `board.show` reads stay before `mint_run_id`, and the `Run`/`StoryRun`/`SubtaskRun` rows are written before the walk. The return dict's keys, values and order are unchanged. `store.close()` stays in `finally`.
- The Typer command, `HANDLED`, `EXIT_ERROR` and `EXIT_ESCALATED` are not touched.
- Out of scope: rollup, `orchestrate.run_milestone`, `am run --milestone`, and addendum section 4 items. Do not add stacked-base or multi-story logic to the driver.
- Verification: `uv run pytest`. There is no typecheck and no lint.

## Review Focus

The spec allows exactly one new test, so the failure modes below are pinned by existing tests, which this refactor must keep green unchanged. They are listed so a reviewer checks them against the diff.

- A subtask that escalates must come back as `summary.status == "escalated"` with `ok: true` and exit 1, not as an exception. Pinned by `tests/test_cli.py::test_an_escalated_subtask_is_ok_true_and_exit_one`. The driver must not wrap `engine.run_subtask` in a `try`.
- The runner's out-of-band warnings must be concatenated after the summary's, not replace them. Pinned by `tests/test_cli.py::test_the_runners_own_warnings_join_the_summarys_in_the_payload`. `getattr(runner, "warnings", [])` must stay, because `fake_runner()` is a bare function with no `warnings` attribute.
- A card whose parent lookup fails must leave no run directory, so the board reads must stay before `mint_run_id`/`Store.open`. Pinned by `tests/test_cli.py::test_a_failing_parent_lookup_is_an_envelope_and_leaves_no_run_directory`.
- The gate parameters must still reach the engine's context. Pinned by `tests/test_cli.py::test_run_card_hands_the_engine_the_gate_parameters_task_yaml_binds`. The driver must pass `extra_context=gate_context(commands, allow_no_verification)`.
- The production factory path must still build a real `dispatch.AgentRunner` with the right ids when `runner_factory is None`. Pinned by `tests/e2e/test_production_wiring.py`. The driver must default to `default_runner_factory` and pass `story_id=parent.id` and `card_id=card.id`.

---

## File Structure

- Modify: `src/agent_manager/cli.py`. Add the `dataclass` import, add `SubtaskDrive` and `drive_subtask` after `gate_context` (ends at line 657), and rewire `run_card` (lines 660-768).
- Modify: `tests/test_cli.py`. Add one Engine-tier test after `test_run_card_hands_the_engine_the_gate_parameters_task_yaml_binds` (ends at line 1405) and before `runner = CliRunner()` (line 1408). Engine-tier tests for `cli` live in this file per its module docstring (lines 1-13) and design spec section 14. The fixtures `project` (1219), `_add_card` (1211), `fake_runner` (1288), `requires_git` (1194) and `requires_brd` (1198) are all defined above that point.

---

### Task 1: Add `drive_subtask` and `SubtaskDrive`, driven by an Engine-tier test

**Files:**
- Modify: `src/agent_manager/cli.py:20-24` (imports) and insert after line 657 (end of `gate_context`)
- Test: `tests/test_cli.py`, inserted after line 1405

**Interfaces:**
- Consumes (existing, in `cli.py`): `WORKFLOW_NAME`, `RunnerFactory`, `default_runner_factory`, `gate_context(commands, allow_no_verification) -> dict[str, Any]`, `load_builtin`, `engine.run_subtask(...) -> engine.SubtaskSummary`, `Store`, `models.Card`, `models.SubtaskRun`.
- Produces:
  - `cli.SubtaskDrive`: `@dataclass(frozen=True)` with `summary: engine.SubtaskSummary` and `warnings: list[str]`.
  - `cli.drive_subtask(*, store: Store, run_id: str, card: models.Card, parent: models.Card, subtask: models.SubtaskRun, repo_dir: Path, commands: Sequence[str] = (), allow_no_verification: bool = False, runner_factory: RunnerFactory | None = None) -> SubtaskDrive`.

- [ ] **Step 1: Write the failing test**

Insert this into `tests/test_cli.py` directly after the end of `test_run_card_hands_the_engine_the_gate_parameters_task_yaml_binds` (after line 1405, before the blank lines preceding `runner = CliRunner()`):

```python
@requires_git
@requires_brd
def test_drive_subtask_drives_two_subtasks_under_one_store_and_run(project):
    """Addendum O4: the driver runs against a store and run id its caller already
    holds, so a milestone runner can drive every subtask of a story under one
    run. Two subtasks, one store, one run id -- and both must land `done`."""
    milestone = _add_card(project, "Milestone 3: orchestration")
    story_id = _add_card(project, "Run a milestone", milestone)
    first_id = _add_card(project, "First subtask", story_id)
    second_id = _add_card(project, "Second subtask", story_id)

    root = cli.resolve_repo_dir(project)
    parent = board.show(story_id, repo_dir=root)
    subtask_cards = [
        board.show(first_id, repo_dir=root),
        board.show(second_id, repo_dir=root),
    ]

    started_at = datetime(2026, 9, 24, 12, 0, 0, tzinfo=timezone.utc)
    run_id = cli.mint_run_id(first_id, started_at)
    store = store_module.Store.open(root, run_id)
    try:
        store.record_run(
            models.Run(
                id=run_id,
                workflow=cli.WORKFLOW_NAME,
                repo_dir=root,
                base_branch="main",
                branch_prefix="m3",
                status="started",
                started_at=started_at,
                config=models.RunConfig(),
            )
        )
        store.record_story(
            models.StoryRun(
                card_id=parent.id,
                title=parent.title,
                level=0,
                status="started",
                tip_branch=dag.task_branch("m3", subtask_cards[-1]),
            )
        )

        drives = []
        for card in subtask_cards:
            branch = dag.task_branch("m3", card)
            subtask = models.SubtaskRun(
                card_id=card.id,
                branch=branch,
                base_branch="main",
                status="started",
                worktree_path=cli.worktree_for(root, branch),
            )
            store.record_subtask(parent.id, subtask)
            drives.append(
                cli.drive_subtask(
                    store=store,
                    run_id=run_id,
                    card=card,
                    parent=parent,
                    subtask=subtask,
                    repo_dir=root,
                    runner_factory=lambda **kwargs: fake_runner(),
                )
            )

        run = store.load_run(run_id)
    finally:
        store.close()

    assert [drive.summary.status for drive in drives] == ["done", "done"]
    assert [drive.warnings for drive in drives] == [[], []]
    assert run is not None
    assert [story.card_id for story in run.stories] == [story_id]
    assert {sub.card_id: sub.status for sub in run.stories[0].subtasks} == {
        first_id: "done",
        second_id: "done",
    }
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_cli.py::test_drive_subtask_drives_two_subtasks_under_one_store_and_run -v`
Expected: FAIL with `AttributeError: module 'agent_manager.cli' has no attribute 'drive_subtask'`. If git or brd is missing, the test is SKIPPED instead. Install them before going on, because a skip proves nothing.

- [ ] **Step 3: Add the `dataclass` import**

In `src/agent_manager/cli.py`, change the stdlib import block at lines 20-24 from:

```python
import json
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
```

to:

```python
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
```

- [ ] **Step 4: Write the minimal implementation**

In `src/agent_manager/cli.py`, insert this directly after the end of `gate_context` (after its `return {...}` block, which ends at line 657) and before `def run_card(`:

```python
@dataclass(frozen=True)
class SubtaskDrive:
    """What one `drive_subtask` call did: the engine's summary, plus every warning.

    `warnings` is the summary's own list followed by the runner's out-of-band
    list. Internal state, so a dataclass rather than a pydantic model.
    """

    summary: engine.SubtaskSummary
    warnings: list[str]


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
) -> SubtaskDrive:
    """Walk one subtask through `builtin/task.yaml` under a store the caller owns.

    Addendum O4's shared driver. `run_card` calls it once, and a milestone runner
    calls it once per subtask against one store and one run id. The caller owns
    everything around the walk: the board reads, the run id, opening and
    closing the store, and the run/story/subtask rows. This function catches
    nothing. An escalation is `summary.status == "escalated"`, not an exception.
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
    summary = engine.run_subtask(
        workflow,
        store,
        story_id=parent.id,
        subtask=subtask,
        repo_dir=repo_dir,
        commands=commands,
        card=card,
        parent_story=parent,
        extra_context=gate_context(commands, allow_no_verification),
        agent_runner=runner,
    )
    # `AgentRunner` collects gate warnings out of band (dispatch.py:375):
    # its signature returns a result, so a warning has nowhere else to go,
    # and dropping them is the §12 failure this whole list exists to prevent.
    warnings = list(summary.warnings) + list(getattr(runner, "warnings", []))
    return SubtaskDrive(summary=summary, warnings=warnings)
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `uv run pytest tests/test_cli.py::test_drive_subtask_drives_two_subtasks_under_one_store_and_run -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "feat(cli): add drive_subtask, the shared per-subtask driver

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2UjNqoWcVtw4c7Kz7SgRZ"
```

---

### Task 2: Make `run_card` call `drive_subtask`

This is a refactor under existing coverage, and the spec forbids new or changed tests for it. The RED signal is the existing `run_card` suite plus `tests/e2e/test_production_wiring.py`. Record them green first, change the code, then confirm they are still green. That shows the rewiring kept the behaviour they pin.

**Files:**
- Modify: `src/agent_manager/cli.py`, `run_card` (lines 660-768 before Task 1; shifted down by Task 1's insertion)

**Interfaces:**
- Consumes: `cli.drive_subtask(...) -> SubtaskDrive` and `SubtaskDrive.summary` / `SubtaskDrive.warnings` from Task 1.
- Produces: no new names. `run_card`'s signature and return dict are unchanged.

- [ ] **Step 1: Record the baseline for the tests that judge this refactor**

Run: `uv run pytest tests/test_cli.py tests/e2e/test_production_wiring.py -q`
Expected: all PASS. This includes the new test from Task 1. Note the pass count so you can compare it after the change.

- [ ] **Step 2: Remove the workflow load from `run_card`'s preamble**

In `run_card`, change:

```python
    started_at = clock()
    run_id = mint_run_id(card.id, started_at)
    workflow = load_builtin(WORKFLOW_NAME)

    store = Store.open(root, run_id)
```

to:

```python
    started_at = clock()
    run_id = mint_run_id(card.id, started_at)

    store = Store.open(root, run_id)
```

- [ ] **Step 3: Replace the driving block with a call to `drive_subtask`**

In `run_card`, replace this block, which runs from the `factory = ...` line through the `return {...}` dict:

```python
        factory = default_runner_factory if runner_factory is None else runner_factory
        runner = factory(
            workflow=workflow,
            store=store,
            run_id=run_id,
            story_id=parent.id,
            card_id=card.id,
        )
        summary = engine.run_subtask(
            workflow,
            store,
            story_id=parent.id,
            subtask=subtask,
            repo_dir=root,
            commands=commands,
            card=card,
            parent_story=parent,
            extra_context=gate_context(commands, allow_no_verification),
            agent_runner=runner,
        )

        store.record_run(run_record.model_copy(update={"status": summary.status}))
        store.record_story(story.model_copy(update={"status": summary.status}))
        store.record_subtask(
            story.card_id, subtask.model_copy(update={"status": summary.status})
        )

        # `AgentRunner` collects gate warnings out of band (dispatch.py:375):
        # its signature returns a result, so a warning has nowhere else to go,
        # and dropping them is the §12 failure this whole list exists to prevent.
        warnings = list(summary.warnings) + list(getattr(runner, "warnings", []))
        return {
```

with:

```python
        drive = drive_subtask(
            store=store,
            run_id=run_id,
            card=card,
            parent=parent,
            subtask=subtask,
            repo_dir=root,
            commands=commands,
            allow_no_verification=allow_no_verification,
            runner_factory=runner_factory,
        )
        summary = drive.summary

        store.record_run(run_record.model_copy(update={"status": summary.status}))
        store.record_story(story.model_copy(update={"status": summary.status}))
        store.record_subtask(
            story.card_id, subtask.model_copy(update={"status": summary.status})
        )

        warnings = drive.warnings
        return {
```

Leave the return dict body (`"run_id"` through `"warnings": warnings`), the `finally: store.close()`, and everything before `store = Store.open(root, run_id)` exactly as they are. `load_builtin` is still used by `drive_subtask` and elsewhere, so keep its import. Confirm that with `grep -n "load_builtin" src/agent_manager/cli.py`: it must still show the import line and the call inside `drive_subtask`.

- [ ] **Step 4: Run the judging tests to verify they still pass**

Run: `uv run pytest tests/test_cli.py tests/e2e/test_production_wiring.py -q`
Expected: all PASS, with the same count as Step 1.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest`
Expected: all PASS, including `tests/e2e`. There is no typecheck or lint step.

- [ ] **Step 6: Confirm no existing test changed**

Run: `git diff --stat HEAD -- tests/`
Expected: no output, because Task 2 touches only `src/agent_manager/cli.py` (Task 1's test is already committed at HEAD). Then check that the only test change on the branch is Task 1's single added function with `git diff m3/task-add-am-run-milestone-36faf21e -- tests/`, which must show only added lines (`+`) for `test_drive_subtask_drives_two_subtasks_under_one_store_and_run`.

- [ ] **Step 7: Commit**

```bash
git add src/agent_manager/cli.py
git commit -m "refactor(cli): run_card drives its subtask through drive_subtask

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2UjNqoWcVtw4c7Kz7SgRZ"
```
