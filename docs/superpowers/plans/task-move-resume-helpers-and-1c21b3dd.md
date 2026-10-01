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

---

# Move resume helpers and error types into runs.py Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move `UnknownRunError`, `NotResumableError`, `CheckpointMismatchError`, `select_resumable`, `orphan_attempts` and `continuable_checkpoint` verbatim from `src/agent_manager/cli.py` into `src/agent_manager/runs.py`, re-exported from `cli` so that `cli.X is runs.X`, with no behavior change.

**Architecture:** This is a pure relocation that follows the pattern subtask 46244d0e set. Each definition is cut out of `cli.py`, pasted unchanged into `runs.py`, and added to `cli.py`'s existing named `from agent_manager.runs import (...)` block. `cli.py` defines a Typer command called `runs`, which is why that block must stay a named import. `runs.py` gains the module imports the moved bodies reference (`models`, `store as store_module`, `runtime.engine as runtime_engine`, `workflow.task as task_workflow`). It still never imports `typer`, `agent_manager.cli` or `agent_manager.harness.launcher` directly.

**Tech Stack:** Python, pytest, uv.

**Spec:** `docs/superpowers/specs/task-move-resume-helpers-and-1c21b3dd-design.md` (reproduced verbatim above).

## Global Constraints

- Work on branch `m13/task-move-resume-helpers-and-1c21b3dd` in worktree `/home/paulomtts/Code/agent-manager/.claude/worktrees/m13/task-move-resume-helpers-and-1c21b3dd`. It was cut from `m13/task-move-the-simple-cli-py-46244d0e`. Do not assume that code from any other subtask (64babfa2, 61a0d9be) exists.
- All paths below are relative to that worktree root.
- Moved definitions stay verbatim: same bodies, docstrings and signatures. `CheckpointMismatchError` keeps inheriting from both `CliError` and `runtime_engine.CheckpointMismatch`, in that order.
- `runs.py` must not import `typer`, `agent_manager.cli` or `agent_manager.harness.launcher`. Loading `runs.py` in a fresh interpreter must not load `typer` or `agent_manager.cli`. Transitively loading `harness.launcher` through `dispatch` is allowed: the existing test checks only the source of `runs.py` for launcher imports.
- `cli.py` re-exports through the existing named `from agent_manager.runs import (...)` block. Never use `from agent_manager import runs` there, because `cli.py` has its own `def runs(...)` command.
- `checkpoint_resume_phase`, the other `CliError` subclasses, `dry_run_payload` and `default_runner_factory` stay in `cli.py`.
- Do not edit `src/agent_manager/orchestrate.py`, `src/agent_manager/bases.py`, `src/agent_manager/integration.py` or `tests/test_cli.py`. Do not add a static import-graph test for `bases`/`integration`.
- `cli.py` keeps its imports of `models`, `store as store_module`, `runtime_engine` and `task_workflow`. All four are still used in `cli.py` after the move: `models.` in `status_rows`/`find_subtask`/`select_attempt`/`logs_payload` and the `run --card` record building, `store_module.` in `checkpoint_resume_phase` and the status/control commands, and `runtime_engine.`/`task_workflow.` in `checkpoint_resume_phase` and the `runtime_engine.run_subtask_async(task_workflow.TASK, ...)` call (cli.py ~665). No import in `cli.py` becomes unused.
- Verification: `uv run pytest` (there is no lint or typecheck command).

## Review Focus

- A monkeypatch of `cli.continuable_checkpoint` must still reach `orchestrate.py`. `tests/test_orchestrate.py:2948` and `:3927` patch `cli.continuable_checkpoint`, and `orchestrate.py:1082` calls `cli.continuable_checkpoint(...)` through attribute lookup on the `cli` module. So a re-export that binds the name in `cli`'s namespace keeps the patch working. Those existing tests pin this, and no new test is needed.
- `except NotResumableError` / `except CheckpointMismatchError` in `cli.py` and `orchestrate.py` must still catch what the moved functions raise. They catch the identical class object, because `select_resumable` in `runs.py` raises `runs.NotResumableError`, which is `cli.NotResumableError`. The `MOVED_NAMES` identity test pins this.
- `resume` on a mismatched checkpoint must still produce envelope `type` `"CheckpointMismatchError"` at exit 3. `error_envelope` uses `type(error).__name__`, which the move leaves alone, and `HANDLED` holds `CliError`, the shared base. The existing `tests/test_cli.py` envelope assertions pin this.
- `from agent_manager import runs` in a fresh interpreter must not load `typer` or `agent_manager.cli` through the new imports. The chain `runtime.engine` -> `pygents`/`runtime.*`/`workflow.phases`, `workflow.task` -> `dispatch`/`results`/`steps.*`, `store` -> `models`/`paths` was checked: none of it imports `cli`, `bases`, `integration`, `orchestrate` or `typer`. The existing `test_importing_runs_loads_neither_typer_nor_cli` pins this.
- `checkpoint_resume_phase` sits between two of the moved functions, so a sloppy cut could drag it along. Task 2's new `test_checkpoint_resume_phase_stays_in_cli` pins it in place.

---

## File Structure

- Modify: `src/agent_manager/runs.py`. It gains three error classes (placed after `RepoDirError`), the four module imports, three functions (appended at the end), and a module docstring extended to cover the resume helpers.
- Modify: `src/agent_manager/cli.py`. The six definitions are removed and their names added to the `from agent_manager.runs import (...)` block at lines ~51-61.
- Modify: `tests/test_runs.py`. `MOVED_NAMES` is extended, and two new tests are added.

---

### Task 1: Move the three error types into runs.py

**Files:**
- Modify: `src/agent_manager/runs.py` (imports at lines 15-17; insert the classes after `RepoDirError` at lines 31-32)
- Modify: `src/agent_manager/cli.py` (import block at lines 51-61; remove `UnknownRunError` at lines 70-78, `NotResumableError` at lines 120-127, and `CheckpointMismatchError` at lines 130-138)
- Test: `tests/test_runs.py`

**Interfaces:**
- Consumes: `runs.CliError` (exists), `agent_manager.runtime.engine.CheckpointMismatch` (an `Exception` subclass at `src/agent_manager/runtime/engine.py:35`).
- Produces: `runs.UnknownRunError(CliError)`, `runs.NotResumableError(CliError)`, `runs.CheckpointMismatchError(CliError, runtime_engine.CheckpointMismatch)`, each also reachable as `cli.<Name>` and identical to it. Task 2's `select_resumable` raises `NotResumableError`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_runs.py`, replace the `MOVED_NAMES` tuple (lines 121-131) with:

```python
MOVED_NAMES = (
    "RUN_ID_TIME_FORMAT",
    "WORKTREE_PARTS",
    "CliError",
    "RepoDirError",
    "resolve_repo_dir",
    "mint_run_id",
    "worktree_for",
    "RunnerFactory",
    "gate_context",
    "UnknownRunError",
    "NotResumableError",
    "CheckpointMismatchError",
)
```

Then append this test at the end of the file, after `test_default_runner_factory_stays_in_cli`:

```python
def test_resume_error_types_are_defined_in_runs():
    from agent_manager.runtime import engine as runtime_engine

    for error_type in (
        runs.UnknownRunError,
        runs.NotResumableError,
        runs.CheckpointMismatchError,
    ):
        assert error_type.__module__ == "agent_manager.runs"
        assert issubclass(error_type, runs.CliError)
    assert issubclass(runs.CheckpointMismatchError, runtime_engine.CheckpointMismatch)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_runs.py -v`
Expected: FAIL. The three new `test_cli_re_exports_the_moved_name_as_the_same_object[UnknownRunError|NotResumableError|CheckpointMismatchError]` cases and `test_resume_error_types_are_defined_in_runs` fail with `AttributeError: module 'agent_manager.runs' has no attribute 'UnknownRunError'` (or the matching name). Every other test in the file passes.

- [ ] **Step 3: Add the runtime_engine import to runs.py**

In `src/agent_manager/runs.py`, replace the import block:

```python
from agent_manager import dag
from agent_manager.runtime.walk import AgentPhaseRunner
from agent_manager.store import Store
```

with:

```python
from agent_manager import dag
from agent_manager.runtime import engine as runtime_engine
from agent_manager.runtime.walk import AgentPhaseRunner
from agent_manager.store import Store
```

- [ ] **Step 4: Paste the three error classes into runs.py**

In `src/agent_manager/runs.py`, directly after the `RepoDirError` class (the line `    """`--repo-dir` does not name a directory this tool can work in."""`), insert these three classes, copied verbatim from `cli.py`:

```python


class UnknownRunError(CliError):
    """`status` was asked for a run this project's projection does not hold.

    A `CliError` so it rides the existing `HANDLED` tuple into an `ok: false`
    envelope at exit 3 rather than reaching the renderer as a `None` tree. The
    same class covers "no most-recent run to default to": both are the same
    refusal -- the command was asked for a run and there is none -- and the
    message is what tells the two apart.
    """


class NotResumableError(CliError):
    """The run was found, and it holds nothing `resume` can pick up.

    Its own type rather than `UnknownRunError`'s: the run and its tree read
    fine, so what an operator does next -- start a fresh `run --card`, wait for
    `retry`, or drive the subtasks one at a time -- depends entirely on the
    status this message names, and a script can branch on the `type` field.
    """


class CheckpointMismatchError(CliError, runtime_engine.CheckpointMismatch):
    """`resume` found a checkpoint saved under another `TASK`.

    A `CliError`, so it rides `HANDLED` to an `ok: false` envelope at exit 3,
    and a `runtime_engine.CheckpointMismatch`, so it is the engine's own
    refusal by type (card 02890d5d). The CLI raises it itself, before any
    write, rather than letting `run_subtask` raise it after the orphan
    attempts and the `started` rows were already recorded.
    """
```

Keep exactly two blank lines between each class and before `def resolve_repo_dir`.

- [ ] **Step 5: Remove the three classes from cli.py and re-export them**

In `src/agent_manager/cli.py`, delete the whole `class UnknownRunError(CliError):` definition (the docstring included), the whole `class NotResumableError(CliError):` definition, and the whole `class CheckpointMismatchError(CliError, runtime_engine.CheckpointMismatch):` definition. Leave `ParentlessCardError`, `UnknownCardError`, `UnknownPhaseError`, `UnknownAttemptError`, `NotRunningError`, `DeadRunError`, `NotAcceptingError` and `RunIsLiveError` where they are, with two blank lines between neighbouring classes.

Then replace the re-export block:

```python
from agent_manager.runs import (
    RUN_ID_TIME_FORMAT,
    WORKTREE_PARTS,
    CliError,
    RepoDirError,
    RunnerFactory,
    gate_context,
    mint_run_id,
    resolve_repo_dir,
    worktree_for,
)
```

with:

```python
from agent_manager.runs import (
    RUN_ID_TIME_FORMAT,
    WORKTREE_PARTS,
    CheckpointMismatchError,
    CliError,
    NotResumableError,
    RepoDirError,
    RunnerFactory,
    UnknownRunError,
    gate_context,
    mint_run_id,
    resolve_repo_dir,
    worktree_for,
)
```

Keep `from agent_manager.runtime import engine as runtime_engine` in `cli.py`: `checkpoint_resume_phase` and the `runtime_engine.run_subtask_async(...)` call (cli.py ~665) still use it.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_runs.py -v`
Expected: PASS, all tests. That includes `test_runs_source_imports_neither_typer_nor_cli_nor_launcher` and `test_importing_runs_loads_neither_typer_nor_cli` with the new `runtime_engine` import.

Run: `uv run pytest tests/test_cli.py tests/test_orchestrate.py -q`
Expected: PASS. This is the regression oracle, and the envelope `type` strings are unchanged.

- [ ] **Step 7: Commit**

```bash
git add src/agent_manager/runs.py src/agent_manager/cli.py tests/test_runs.py
git commit -m "refactor(runs): move the resume error types out of cli.py (S1, 1c21b3dd)"
```

---

### Task 2: Move select_resumable, orphan_attempts and continuable_checkpoint into runs.py

**Files:**
- Modify: `src/agent_manager/runs.py` (module docstring at lines 1-8, the import block, and new functions appended at the end of the file)
- Modify: `src/agent_manager/cli.py` (import block; remove `select_resumable` at ~lines 434-479 and `orphan_attempts` at ~lines 482-498, both shifted up about 27 lines after Task 1; remove `continuable_checkpoint` at ~lines 545-560, also shifted)
- Test: `tests/test_runs.py`

**Interfaces:**
- Consumes: `runs.NotResumableError` (Task 1), `runs.runtime_engine` (Task 1 import), `agent_manager.models.Run`/`StoryRun`/`SubtaskRun`/`PhaseRun`/`Attempt`, `agent_manager.store.Store.latest_open_checkpoint(card_id: str, workflow: str) -> Checkpoint | None`, `agent_manager.store.Checkpoint`, `agent_manager.workflow.task.TASK` (`.name`, `.digest()`), and `runtime_engine.pending_phase(checkpoint) -> str | None`.
- Produces: `runs.select_resumable(run: models.Run) -> tuple[models.StoryRun, models.SubtaskRun]`, `runs.orphan_attempts(subtask: models.SubtaskRun) -> list[tuple[models.PhaseRun, models.Attempt]]`, and `runs.continuable_checkpoint(store: Store, card_id: str) -> store_module.Checkpoint | None`, each identical to `cli.<name>`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_runs.py`, replace the `MOVED_NAMES` tuple with:

```python
MOVED_NAMES = (
    "RUN_ID_TIME_FORMAT",
    "WORKTREE_PARTS",
    "CliError",
    "RepoDirError",
    "resolve_repo_dir",
    "mint_run_id",
    "worktree_for",
    "RunnerFactory",
    "gate_context",
    "UnknownRunError",
    "NotResumableError",
    "CheckpointMismatchError",
    "select_resumable",
    "orphan_attempts",
    "continuable_checkpoint",
)
```

Then append this test at the end of the file:

```python
def test_checkpoint_resume_phase_stays_in_cli():
    from agent_manager import cli

    assert cli.checkpoint_resume_phase.__module__ == "agent_manager.cli"
    assert not hasattr(runs, "checkpoint_resume_phase")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_runs.py -v`
Expected: FAIL. `test_cli_re_exports_the_moved_name_as_the_same_object[select_resumable]`, `[orphan_attempts]` and `[continuable_checkpoint]` fail with `AttributeError: module 'agent_manager.runs' has no attribute 'select_resumable'` (or the matching name). `test_checkpoint_resume_phase_stays_in_cli` already passes, since it is a guard against over-moving and stays green through Step 6. Everything else passes.

- [ ] **Step 3: Add the remaining imports and extend the docstring in runs.py**

In `src/agent_manager/runs.py`, replace the module docstring:

```python
"""Run helpers with no Typer in them (architecture cleanup, decision S1).

`cli.py` composes and renders; the plain pieces a run needs -- where its repo
is, what it is called, where its worktree goes, how a runner is made, and which
gate parameters it binds -- live here so the modules downstream of `cli` can
reach them without importing the Typer app. `cli.py` re-exports every name
defined here, so `cli.X is runs.X`. This module never imports `cli`.
"""
```

with:

```python
"""Run helpers with no Typer in them (architecture cleanup, decision S1).

`cli.py` composes and renders; the plain pieces a run needs -- where its repo
is, what it is called, where its worktree goes, how a runner is made, which
gate parameters it binds, and how an interrupted run is picked back up (which
subtask is resumable, which attempts were orphaned, which checkpoint a relaunch
continues from, and the refusals those raise) -- live here so the modules
downstream of `cli` can reach them without importing the Typer app. `cli.py`
re-exports every name defined here, so `cli.X is runs.X`. This module never
imports `cli`.
"""
```

Then replace the import block:

```python
from agent_manager import dag
from agent_manager.runtime import engine as runtime_engine
from agent_manager.runtime.walk import AgentPhaseRunner
from agent_manager.store import Store
```

with:

```python
from agent_manager import dag, models, store as store_module
from agent_manager.runtime import engine as runtime_engine
from agent_manager.runtime.walk import AgentPhaseRunner
from agent_manager.store import Store
from agent_manager.workflow import task as task_workflow
```

- [ ] **Step 4: Append the three functions to runs.py**

At the end of `src/agent_manager/runs.py`, after `gate_context`, append these functions, copied verbatim from `cli.py`:

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


def orphan_attempts(
    subtask: models.SubtaskRun,
) -> list[tuple[models.PhaseRun, models.Attempt]]:
    """Every attempt recorded `started` with no terminal event, in tree order.

    §9's "in-flight attempt": the manager was killed between the row that says a
    dispatch began and the row that says how it ended. The owning phase comes
    back with it because `Store.record_attempt` is keyed by phase name and an
    `Attempt` carries no back-reference, exactly as `find_subtask` returns the
    owning story.
    """
    return [
        (phase, attempt)
        for phase in subtask.phases
        for attempt in phase.attempts
        if attempt.status == "started"
    ]


def continuable_checkpoint(
    store: Store, card_id: str
) -> store_module.Checkpoint | None:
    """The open checkpoint a pygents relaunch continues `card_id` from, or `None`.

    `Store.latest_open_checkpoint` across every run, for `TASK`'s name. A row
    saved under another digest, or one holding no turn (a phase escalation,
    see `runtime_engine.pending_phase`), is `None` too: a relaunch never
    refuses, it starts the card from its first phase (card 02890d5d).
    """
    found = store.latest_open_checkpoint(card_id, task_workflow.TASK.name)
    if found is None or found.digest != task_workflow.TASK.digest():
        return None
    if runtime_engine.pending_phase(found) is None:
        return None
    return found
```

- [ ] **Step 5: Remove the three functions from cli.py and re-export them**

In `src/agent_manager/cli.py`, delete the whole `def select_resumable(run: models.Run) -> ...` definition, the whole `def orphan_attempts(...)` definition, and the whole `def continuable_checkpoint(...)` definition, docstrings included. Leave `def checkpoint_resume_phase(...)` untouched, with two blank lines before it (after `logs_payload`'s closing `}`) and two blank lines after it (before `app = typer.Typer(`).

Then replace the re-export block:

```python
from agent_manager.runs import (
    RUN_ID_TIME_FORMAT,
    WORKTREE_PARTS,
    CheckpointMismatchError,
    CliError,
    NotResumableError,
    RepoDirError,
    RunnerFactory,
    UnknownRunError,
    gate_context,
    mint_run_id,
    resolve_repo_dir,
    worktree_for,
)
```

with:

```python
from agent_manager.runs import (
    RUN_ID_TIME_FORMAT,
    WORKTREE_PARTS,
    CheckpointMismatchError,
    CliError,
    NotResumableError,
    RepoDirError,
    RunnerFactory,
    UnknownRunError,
    continuable_checkpoint,
    gate_context,
    mint_run_id,
    orphan_attempts,
    resolve_repo_dir,
    select_resumable,
    worktree_for,
)
```

Do not remove `models`, `store as store_module`, `runtime_engine` or `task_workflow` from `cli.py`'s imports. `checkpoint_resume_phase`, `status_rows`, `find_subtask`, the `runtime_engine.run_subtask_async(task_workflow.TASK, ...)` call and the status/control commands still use them. `resume_run` keeps calling the bare names `select_resumable(run)` and `orphan_attempts(subtask)` (cli.py ~1381/1384), which now resolve to the re-exported objects.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_runs.py -v`
Expected: PASS, all tests. That includes the 15 `MOVED_NAMES` identity cases, `test_resume_error_types_are_defined_in_runs`, `test_checkpoint_resume_phase_stays_in_cli`, `test_runs_source_imports_neither_typer_nor_cli_nor_launcher` and `test_importing_runs_loads_neither_typer_nor_cli`.

Run: `uv run pytest tests/test_cli.py tests/test_orchestrate.py -q`
Expected: PASS. The regression oracle covers `test_select_resumable_*`, `test_orphan_attempts_are_exactly_the_ones_recorded_started`, `test_continuable_checkpoint_is_the_open_matching_row_or_none`, the envelope-type assertions, and the `monkeypatch.setattr(cli, "continuable_checkpoint", ...)` tests in `test_orchestrate.py`.

- [ ] **Step 7: Commit**

```bash
git add src/agent_manager/runs.py src/agent_manager/cli.py tests/test_runs.py
git commit -m "refactor(runs): move the resume helpers out of cli.py (S1, 1c21b3dd)"
```

---

### Task 3: Full-suite verification

**Files:**
- None modified.

**Interfaces:**
- Consumes: everything from Tasks 1-2.
- Produces: a green default suite.

- [ ] **Step 1: Confirm the out-of-scope files are untouched**

Run: `git diff m13/task-move-the-simple-cli-py-46244d0e --stat`
Expected: only `src/agent_manager/runs.py`, `src/agent_manager/cli.py`, `tests/test_runs.py` and the `docs/superpowers/` spec/plan files are listed. No `orchestrate.py`, `bases.py`, `integration.py` or `tests/test_cli.py`.

- [ ] **Step 2: Run the full suite**

Run: `uv run pytest`
Expected: PASS. All default-suite tests pass, and the `e2e` tests are deselected by `addopts`.

If anything fails, stop and use superpowers:systematic-debugging. Do not edit `tests/test_cli.py` to make it pass, because it is the regression oracle.
