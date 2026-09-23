<!-- task-pipeline: validated -->
# Spec (verbatim)

# Subtask 5524ae72 — `agent-manager resume <run-id>`

Parent story 1a46ab5a ("The CLI: run, status, logs, resume"), milestone 352e955b. Siblings `run --card`, `status`/`runs` and `logs` are done; this card adds the fourth verb. Source of truth for the semantics: design spec §9 ("Resume semantics", lines 370-386) and §10's grammar line `agent-manager resume <run-id>`.

## Scope

Owns `src/agent_manager/cli.py` only. No change to `store.py`, `engine.py`, `models.py`, `dispatch.py` or `workflow/builtin/task.yaml`, and no change to the observable behaviour of `run`, `status`, `runs` or `logs`.

In particular, "discarding" an in-flight attempt is done with the existing `Store.record_attempt`, re-recording the orphan `Attempt` with a terminal `harness_error` status. That keeps the journal-before-row ordering, needs no new `EventKind`, and leaves `replay`/`rebuild_from_journal` unchanged — the attempt is just one more `attempt_upsert` keyed by `(phase, n)`. No new store method, no delete path, no journal schema change.

Out of scope, explicitly: `retry`, `cancel`, `watch`, `--harness`, `--dry-run`, `--workflow`, milestone orchestration, non-Claude harnesses, and persisting new fields on `RunConfig`.

## Behaviour

`agent-manager resume <RUN_ID> [--repo-dir .] [--allow-no-verification] [--pretty]`, plus a testable `resume_run(run_id, *, repo_dir, allow_no_verification=False, commands=(), runner_factory=None, clock=_utcnow) -> dict[str, Any]` mirroring `run_card` (cli.py:458) so the tests drive the function and the Typer command stays a thin wrapper like `run` (cli.py:585).

Sequence:

1. `resolve_repo_dir(--repo-dir)`, then load the run out of the project projection the way `logs_for` does (`store_module.open_db` + `store_module.load_run`, closed on every path) — never `Store.open`, which would mint a run directory for a run that does not exist. Missing run → `UnknownRunError`, with `logs_for`'s wording.
2. Pick the subtask: the single `SubtaskRun` in the run tree whose status is `started`. Nothing `started` → `NotResumableError` (new `CliError` subclass) naming the subtask's actual status: a `done` run has nothing to resume, and an `escalated` run is `retry`'s job, not this card's. More than one `started` subtask → `NotResumableError` too: multi-subtask runs are the milestone runner's and this command drives one subtask, the way `run --card` does.
3. Re-fetch the cards with `board.show` for the subtask's `card_id` and its story's `card_id` — `engine.run_subtask` needs real `models.Card` values for `card`/`parent_story`, and the projection stores only ids. Board errors ride `HANDLED` as they already do in `run_card`.
4. Load the workflow with `load_builtin(run.workflow)` (`run` only ever writes `task`; anything else surfaces as the loader's own `WorkflowLoadError`).
5. Open `Store.open(root, run.id)` — the same run id, so journal lines continue the existing sequence and `dispatch.next_attempt` keeps numbering attempt directories forward instead of overwriting the crashed one.
6. Discard in-flight attempts: for every phase of that subtask, every `Attempt` with `status == "started"` is re-recorded via `store.record_attempt(story_id, card_id, phase.name, attempt.model_copy(update={"status": "harness_error"}))`. Journal first, row after, as `Store.record_attempt` already does. Attempt directories on disk are left alone — their prompt/stdout are the evidence `logs` prints.
7. Choose the restart phase: the interrupted phase, then backed off over result dependencies (see below).
8. Re-record run/story/subtask as `started` (`record_run`/`record_story`/`record_subtask`, same values with the status forced) before the walk, for the same reason `run_card` writes them first: a resume that dies on its first phase must still be visible to `status`.
9. Build the runner through the `RunnerFactory` seam (`default_runner_factory` unless injected, cli.py:393-435) with the existing `run_id`/`story_id`/`card_id`, and call `engine.run_subtask(..., subtask=<the stored SubtaskRun>, repo_dir=root, commands=commands, card=card, parent_story=parent, extra_context=gate_context(commands, allow_no_verification), agent_runner=runner, start_phase=<chosen phase>)`. Branch, base branch and worktree path come from the stored `SubtaskRun`, not from flags: that is what §9 means by the run recording what it was started with.
10. Record the final status on run, story and subtask exactly as `run_card` does, close the store in a `finally`, and return the payload.

### Choosing the restart phase

Two pure helpers in `cli.py`, both operating on a `models.SubtaskRun` plus the loaded `Workflow`:

- **Interrupted phase.** The first phase of the workflow that is recorded `started` (no terminal status) — the crash signature §9 describes. If no phase is `started` (the process died between phases), it is the first workflow phase not recorded `done`, except that `skip_to` phases are never recorded (engine.py:432 only appends to the in-memory `summary.skipped`), so when the first not-done phase lies after a recorded-`done` phase carrying `skip_to` (`plan_check`), the restart phase is that `skip_to` phase, letting its `when` re-decide the skip instead of re-running `spec`/`plan` over a validated plan. If every phase is `done`, `NotResumableError`, because step 2's `started` subtask with a complete phase list means only the final status write was lost and re-running `mark_done` would be a fresh run, not a resume.
- **Back-off over result dependencies.** Earlier phases' results live only in the in-memory binding table `engine.run_subtask` builds (`_bind_result`, engine.py:439) and are not replayed from the journal. So a restart phase whose declared `inputs` name an earlier *phase* (rather than a key `subtask_context`/`_document_paths`/`gate_context` supplies) must be backed off to that producing phase. In `workflow/builtin/task.yaml` the only such edge is `spec`'s `inputs: [card, explore]`, so an interrupted `spec` restarts at `explore`; `spec_path`/`plan_path` are computed per subtask by `_document_paths` and create no edge, and `plan_check`'s `when` reads only its own result. The helper derives this from the document rather than hard-coding the pair, and backs off transitively. Re-running `explore` is safe under §9: every phase is idempotent or re-entrant (`worktree.ensure` never resets, `rollup.set_status` is idempotent, `verify.run_suite` is read-only, `spec`/`plan` overwrite their file, `implement` re-enters through the Plan-Hash trailer).

### Configuration not recoverable from the record

`models.RunConfig` carries `max_concurrent_stories`, `dry_run`, `launcher`, `harness_map` — not the suite commands and not `--allow-no-verification`. Rather than grow the model (out of scope, and it would change what `run` journals), `resume` accepts `--allow-no-verification` itself and, like `run`, discovers no suite: `commands` defaults to empty and `gate_context(commands, allow_no_verification)` is built the same way. The flag's absence therefore means the same on resume as on a fresh `run`.

### Output

The `{ok, data}` envelope, one line by default and indented under `--pretty`, via the existing `ok_envelope`/`error_envelope`/`render`. The payload is `run_card`'s keys (`run_id`, `card_id`, `story_id`, `branch`, `base_branch`, `worktree`, `status`, `failed_phase`, `detail`, `skipped`, `warnings`) plus two resume-specific ones: `resumed_from` (the phase the walk restarted at) and `discarded_attempts` (a list of `{"phase": ..., "n": ...}` for the orphans closed in step 6). Warnings are collected the same way, summary warnings plus `getattr(runner, "warnings", [])`.

## Error paths

- Unknown run id, or a run absent from this project's projection → `UnknownRunError`, `ok: false`, exit `EXIT_ERROR` (3).
- Nothing to resume (no `started` subtask, subtask `done`/`escalated`, or every phase `done`), or more than one `started` subtask → `NotResumableError` (new `CliError` subclass, so it rides `HANDLED`), exit 3, message naming the status found and pointing at `agent-manager status <run-id>`.
- `--repo-dir` not a directory → `RepoDirError`, exit 3, as everywhere else.
- Board failure re-fetching the cards, workflow load failure, `EngineError`, `ValueError` → exit 3 via the existing `HANDLED` tuple (cli.py:569). Nothing new is added to `HANDLED` beyond what the new `CliError` subclass inherits.
- A resumed walk that escalates prints an `ok: true` envelope with `status == "escalated"` and exits `EXIT_ESCALATED`, exactly as `run` does.
- Refusals must write nothing: no `runs/<run-id>` directory, no journal, no rows — the store is only opened after the run, subtask and cards have all resolved.

## Tests

Tier rule is design §14 (lines 477-492) as applied in `tests/test_cli.py`'s module docstring. Everything below lands in `tests/test_cli.py`; there are no `tests/test_store.py` or `tests/test_engine.py` additions, because no store or engine code changes.

Unit tier (hand-built `models` objects, no clock, no filesystem, no subprocess — the `_pure_run`/`_pure_dispatch` style at test_cli.py:95-115):

1. Interrupted-phase selection returns the phase recorded `started` with a non-terminal attempt.
2. Interrupted-phase selection with no `started` phase returns the first phase not recorded `done` (crash between phases).
3. Interrupted-phase selection with every phase `done` reports "nothing to resume" (the condition `resume_run` turns into `NotResumableError`).
4. Back-off: an interrupted `spec` yields `explore`, because `spec` declares `explore` as an input; an interrupted `implement` yields `implement`, because its inputs are all context-supplied.
5. Orphan selection lists exactly the attempts recorded `started`, across phases, and leaves terminal ones alone.

Engine tier (fake runner injected via `runner_factory=lambda **kw: ...`, on Steps-tier fixtures: `project`, `cards`, `projection`, `requires_git`, `requires_brd`, `XDG_DATA_HOME` → `tmp_path`; never a real harness):

6. **The §14 crash-and-resume test.** Drive `cli.run_card` with a fake runner that, on the `implement` phase, records an attempt `started` through the real store and then raises `KeyboardInterrupt` — a `BaseException`, so it escapes `engine.run_subtask`'s `except Exception` the way a killed process does, leaving the subtask `started`, the phase `started` and an attempt `started`. Then call `resume_run` with a well-behaved fake runner and assert: payload `status == "done"`, `resumed_from == "implement"`, `discarded_attempts` names that attempt, and `_attempt_rows(project)` (test_cli.py:1872) holds no row with status `started`.
7. Resume numbers new attempts forward: after the crash-and-resume of test 6, the crashed attempt's row and directory still exist and the resumed dispatch used a higher `n` (no attempt directory was overwritten).
8. Resume launches no harness: `cli.run_direct` and `cli.dispatch.AgentRunner` monkeypatched to raise, resume completes on the injected fake — the pattern of `test_no_harness_is_ever_launched` (test_cli.py:1262).
9. Resume of a run whose `spec` phase was in flight restarts at `explore`, and the fake runner is asked for `explore` before `spec` (the back-off rule observed end to end, so the binding table `spec` needs is populated).
10. CLI envelope and exits, invoked through `runner.invoke(cli.app, ["resume", ...])`: unknown run id → exit 3 with `error.type == "UnknownRunError"`; a `done` run and an `escalated` run → exit 3 with `error.type == "NotResumableError"`; a resumed walk that escalates → `ok: true`, `status == "escalated"`, exit `EXIT_ESCALATED`; `--pretty` indents the envelope.
11. Refusals write nothing: an unknown-run resume leaves the `runs` tree and the `attempts` rows byte-identical (`_runs_snapshot` / `_attempt_rows`, the style of `test_logs_writes_nothing`, test_cli.py:1883).

No end-to-end real-harness test is added here; §14 keeps that slow, opt-in and out of the default suite. Verification is `uv run pytest`.

---

# `agent-manager resume <run-id>` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `resume <RUN_ID>` command that picks one killed run back up at the phase it died in, discarding the in-flight attempt and driving the subtask to completion.

**Architecture:** Everything lands in `src/agent_manager/cli.py`. Four pure helpers (`select_resumable`, `interrupted_phase`, `resume_start_phase`, `orphan_attempts`) answer "what is there to resume" out of the tree `store_module.load_run` already assembles; `resume_run` composes them the way `run_card` composes `board`/`dag`/`Store`/`engine`, re-opens `Store.open(root, run.id)` so the journal sequence and `dispatch.next_attempt` continue, closes every orphan `Attempt` by re-recording it `harness_error`, and calls `engine.run_subtask(..., start_phase=...)`. The Typer command is a thin wrapper over it, exactly like `run`.

**Tech Stack:** Python 3, Typer, Pydantic v2, SQLite (`store.py`'s projection), pytest, `uv`.

**Spec:** `docs/superpowers/specs/task-add-resume-5524ae72-design.md` (prepended verbatim above). Design spec §9/§10/§14: `docs/superpowers/specs/2026-09-23-agent-manager-design.md`.

## Global Constraints

- Owns `src/agent_manager/cli.py` only. No change to `store.py`, `engine.py`, `models.py`, `dispatch.py`, `paths.py` or `workflow/builtin/task.yaml`.
- No new `EventKind`, no new store method, no delete path, no journal schema change: an orphan attempt is discarded by re-recording it through the existing `Store.record_attempt` with `status="harness_error"`.
- No change to the observable behaviour of `run`, `status`, `runs` or `logs`.
- Out of scope: `retry`, `cancel`, `watch`, `--harness`, `--dry-run`, `--workflow`, milestone orchestration, non-Claude harnesses, new `RunConfig` fields.
- Output is brd's envelope: `{"ok": true, "data": ...}` / `{"ok": false, "error": {"type", "message"}}`, one line by default, indented under `--pretty`.
- Exit codes: `0` finished, `EXIT_ESCALATED` (1) escalated, `EXIT_ERROR` (3) refused. `2` stays Typer's.
- Refusals write nothing: no `runs/<run-id>` directory, no journal line, no row. `Store.open` is reached only after the run, the subtask and both cards have resolved.
- Every new test lands in `tests/test_cli.py`. No `tests/test_store.py` or `tests/test_engine.py` change, because no store or engine code changes. No real harness is ever launched.
- Verification: `uv run pytest`.

## Review Focus

- **More than one `started` subtask** (a milestone-shaped run reaching a command that drives one subtask): must refuse by name rather than pick arbitrarily — Task 1, `test_select_resumable_refuses_more_than_one_subtask_in_flight`.
- **A deterministic phase killed mid-suite** (`verify` recorded `started`, no attempts at all): must restart at `verify` itself, back off nowhere, and report no discarded attempts — Task 2, `test_a_deterministic_phase_killed_mid_suite_restarts_at_itself_with_no_orphans`.
- **A restart at `plan_check` whose `when` now finds no validated plan**: the walk falls through to `spec`, whose `explore` input nothing in a fresh context supplies — must surface as the engine's own `EngineError` (an envelope at exit 3), never a traceback — Task 3, `test_a_restart_at_plan_check_that_finds_no_plan_is_an_engine_error_not_a_traceback`.
- **A run recorded with a workflow name no builtin matches** (a hand-written or older projection row): must surface as `WorkflowLoadError` at exit 3 — Task 4, `test_a_run_recorded_with_an_unknown_workflow_is_an_envelope`.
- **`--repo-dir` that is not a directory**: must be `RepoDirError` at exit 3 before any projection is opened, as everywhere else — Task 4, `test_resume_with_a_repo_dir_that_is_not_a_directory_is_an_envelope`.

## File Structure

- `src/agent_manager/cli.py` — modified. New: `NotResumableError` (after `UnknownAttemptError`, cli.py:97-104), the four pure helpers (after `logs_payload`, cli.py:339-370, so they sit with the other tree-readers), `resume_run` and the `resume` command (at the end of the file, after `logs`, so the module keeps its `run` → `status` → `runs` → `logs` → `resume` order, matching §10's grammar).
- `tests/test_cli.py` — modified. New unit-tier tests beside the existing `_pure_*` helpers, new Engine-tier tests after `test_logs_writes_nothing`.

No new files: the spec's scope is "cli.py only", and the codebase keeps one test module per source module.

---

### Task 1: `NotResumableError` and picking the subtask

**Files:**
- Modify: `src/agent_manager/cli.py` (add `NotResumableError` after `UnknownAttemptError`, cli.py:97-104; add `select_resumable` after `logs_payload`, cli.py:370)
- Test: `tests/test_cli.py` (unit tier, beside `_pure_run`/`_pure_story`/`_pure_subtask`, test_cli.py:99-360)

**Interfaces:**
- Consumes: `models.Run`, `models.StoryRun`, `models.SubtaskRun`; `cli.CliError`, `cli.HANDLED`; test helpers `_pure_run`, `_pure_story`, `_pure_subtask`.
- Produces: `cli.NotResumableError(CliError)`; `cli.select_resumable(run: models.Run) -> tuple[models.StoryRun, models.SubtaskRun]`, raising `NotResumableError` when the count of `started` subtasks is not exactly one.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli.py`, immediately after `test_find_subtask_takes_the_first_match_when_a_card_id_is_duplicated` (ends at test_cli.py:360):

```python
def test_not_resumable_is_a_cli_error_and_rides_the_handled_tuple():
    """A refusal `resume` raises has to reach the operator as an envelope, and
    `HANDLED` is the only thing that turns an exception into one."""
    assert issubclass(cli.NotResumableError, cli.CliError)
    assert isinstance(cli.NotResumableError("nothing in flight"), cli.HANDLED)


def test_select_resumable_returns_the_single_started_subtask():
    done = _pure_subtask("card-1", []).model_copy(update={"status": "done"})
    started = _pure_subtask("card-2", [])
    run = _pure_run([_pure_story("story-1", [done]), _pure_story("story-2", [started])])

    story, subtask = cli.select_resumable(run)

    assert story.card_id == "story-2"
    assert subtask is started


def test_select_resumable_refuses_a_run_with_nothing_in_flight():
    """A `done` run has nothing to pick up, and the message has to name the
    status found: that is what tells an operator to start a fresh run rather
    than to go looking for a lost process."""
    done = _pure_subtask("card-1", []).model_copy(update={"status": "done"})
    run = _pure_run([_pure_story("story-1", [done])])

    with pytest.raises(cli.NotResumableError) as caught:
        cli.select_resumable(run)

    assert "card-1=done" in str(caught.value)
    assert "agent-manager status" in str(caught.value)


def test_select_resumable_refuses_an_escalated_subtask_by_name():
    """An escalation is a full stop a human reads (§12). Re-running it is
    `retry`'s job, not this command's, so the status is named rather than
    silently resumed."""
    escalated = _pure_subtask("card-1", []).model_copy(update={"status": "escalated"})
    run = _pure_run([_pure_story("story-1", [escalated])])

    with pytest.raises(cli.NotResumableError) as caught:
        cli.select_resumable(run)

    assert "card-1=escalated" in str(caught.value)


def test_select_resumable_refuses_more_than_one_subtask_in_flight():
    """Review Focus: a milestone-shaped run reaching a command that drives one
    subtask. Picking one arbitrarily would leave the others recorded `started`
    forever with nothing driving them."""
    first = _pure_subtask("card-1", [])
    second = _pure_subtask("card-2", [])
    run = _pure_run([_pure_story("story-1", [first, second])])

    with pytest.raises(cli.NotResumableError) as caught:
        cli.select_resumable(run)

    message = str(caught.value)
    assert "card-1" in message
    assert "card-2" in message
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k "not_resumable or select_resumable" -v`
Expected: FAIL — `AttributeError: module 'agent_manager.cli' has no attribute 'NotResumableError'` (and the same for `select_resumable`).

- [ ] **Step 3: Add the error type**

In `src/agent_manager/cli.py`, immediately after the `UnknownAttemptError` class (which ends at cli.py:104, before `def resolve_repo_dir`):

```python
class NotResumableError(CliError):
    """The run was found, and it holds nothing `resume` can pick up.

    Its own type rather than `UnknownRunError`'s: the run and its tree read
    fine, so what an operator does next -- start a fresh `run --card`, wait for
    `retry`, or drive the subtasks one at a time -- depends entirely on the
    status this message names, and a script can branch on the `type` field.
    """
```

- [ ] **Step 4: Add `select_resumable`**

In `src/agent_manager/cli.py`, immediately after `logs_payload` (which ends at cli.py:370, before `app = typer.Typer(`):

```python
def select_resumable(run: models.Run) -> tuple[models.StoryRun, models.SubtaskRun]:
    """The one subtask of `run` that was in flight, or a refusal naming why not.

    Pure over the tree `load_run` assembled, like `find_subtask`: which subtask
    is resumable is a question about recorded state, and answering it before any
    store is opened is what keeps a refusal from minting a run directory.

    Exactly one `started` subtask is the resumable shape. Zero means the run
    finished, escalated or never started, and the statuses are listed because
    the fix differs for each. More than one is a milestone-shaped run: this
    command drives one subtask the way `run --card` does, and choosing between
    them would leave the rest recorded `started` with nothing driving them.
    """
    started = [
        (story, subtask)
        for story in run.stories
        for subtask in story.subtasks
        if subtask.status == "started"
    ]
    if len(started) == 1:
        return started[0]
    if not started:
        found = (
            ", ".join(
                f"{subtask.card_id}={subtask.status}"
                for story in run.stories
                for subtask in story.subtasks
            )
            or "no subtask at all"
        )
        raise NotResumableError(
            f"run {run.id!r} has no subtask recorded 'started', so there is no work"
            f" in flight to pick up (found: {found});"
            f" `agent-manager status {run.id}` shows the run as it stands"
        )
    cards = ", ".join(subtask.card_id for _story, subtask in started)
    raise NotResumableError(
        f"run {run.id!r} has {len(started)} subtasks recorded 'started' ({cards}),"
        " and `resume` drives one subtask the way `run --card` does;"
        f" `agent-manager status {run.id}` shows all of them"
    )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k "not_resumable or select_resumable" -v`
Expected: PASS (5 tests).

- [ ] **Step 6: Run the whole suite**

Run: `uv run pytest`
Expected: PASS, no regression in `run`/`status`/`runs`/`logs`.

- [ ] **Step 7: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "feat(cli): pick the one resumable subtask out of a run tree"
```

---

### Task 2: Choosing the restart phase and the orphan attempts

**Files:**
- Modify: `src/agent_manager/cli.py` (add `interrupted_phase`, `_skipped_origin`, `resume_start_phase`, `orphan_attempts` after `select_resumable`)
- Test: `tests/test_cli.py` (unit tier, after the Task 1 tests)

**Interfaces:**
- Consumes: `cli.select_resumable` (Task 1); `workflow.loader.Workflow`, `load_builtin`; `engine.RESERVED_CONTEXT_KEYS`; `models.SubtaskRun`, `models.PhaseRun`, `models.Attempt`.
- Produces:
  - `cli.interrupted_phase(subtask: models.SubtaskRun, workflow: Workflow) -> str | None` — `None` means every phase is `done`.
  - `cli.resume_start_phase(workflow: Workflow, phase_name: str) -> str`
  - `cli.orphan_attempts(subtask: models.SubtaskRun) -> list[tuple[models.PhaseRun, models.Attempt]]`

- [ ] **Step 1: Write the failing tests**

First add the loader import to `tests/test_cli.py`. The import block at test_cli.py:27-30 currently reads:

```python
from agent_manager import board, cli, dag, models, paths, store as store_module
from agent_manager.errors import AgentPhaseFailed, EngineError
from agent_manager.steps.reducers import verification_gate
from agent_manager.workflow.registry import WorkflowLoadError
```

Replace it with:

```python
from agent_manager import board, cli, dag, dispatch, models, paths, store as store_module
from agent_manager.errors import AgentPhaseFailed, EngineError
from agent_manager.steps.reducers import verification_gate
from agent_manager.workflow.loader import load_builtin
from agent_manager.workflow.registry import WorkflowLoadError
```

(`dispatch` is used by Task 3's recording runner; importing it now keeps the import block edited once.)

Then append after the Task 1 tests:

```python
AGENT_PHASE_NAMES = frozenset(
    {"explore", "spec", "validate_spec", "plan", "validate_plan", "implement", "review"}
)
"""Which phases of `builtin/task.yaml` are `kind: agent`. `PhaseRun.kind` is a
Literal, so a hand-built phase has to name the right one."""


def _task_workflow():
    """The shipped `builtin/task.yaml`, loaded and resolved.

    Still unit tier: the only thing read is a packaged document that ships with
    the source. No run state, no clock, no subprocess -- and the back-off rule
    these tests pin is a property of *that* document, so substituting a
    hand-written one would test the wrong thing.
    """
    return load_builtin("task")


def _recorded(name: str, status: str, attempts: list[models.Attempt] | None = None):
    """One `PhaseRun` of the task workflow as the projection would hold it."""
    return models.PhaseRun(
        name=name,
        kind="agent" if name in AGENT_PHASE_NAMES else "deterministic",
        status=status,
        attempts=list(attempts or []),
    )


def test_the_interrupted_phase_is_the_one_recorded_started():
    subtask = _pure_subtask(
        "card-1",
        [
            _recorded("explore", "done", [_pure_attempt(1)]),
            _recorded("mark_in_progress", "done"),
            _recorded("worktree", "done"),
            _recorded("plan_check", "done"),
            _recorded("spec", "done", [_pure_attempt(1)]),
            _recorded("validate_spec", "done", [_pure_attempt(1)]),
            _recorded("plan", "done", [_pure_attempt(1)]),
            _recorded("validate_plan", "done", [_pure_attempt(1)]),
            _recorded("implement", "started", [_pure_attempt(1, status="started")]),
        ],
    )

    assert cli.interrupted_phase(subtask, _task_workflow()) == "implement"


def test_a_crash_between_phases_restarts_at_the_first_phase_not_done():
    """No phase is `started`, so the process died between two of them: the first
    phase the projection does not hold as `done` is the one that never ran."""
    subtask = _pure_subtask(
        "card-1",
        [
            _recorded("explore", "done", [_pure_attempt(1)]),
            _recorded("mark_in_progress", "done"),
            _recorded("worktree", "done"),
        ],
    )

    assert cli.interrupted_phase(subtask, _task_workflow()) == "plan_check"


def test_a_skipped_stretch_restarts_at_the_phase_whose_when_decided_the_skip():
    """`plan_check` may `skip_to: implement`, and a skipped phase is never
    recorded (engine.py:432 only appends to the in-memory summary). Restarting at
    `spec` would re-author a spec over a plan Validate already signed; restarting
    at `plan_check` lets its own `when` decide the jump again."""
    subtask = _pure_subtask(
        "card-1",
        [
            _recorded("explore", "done", [_pure_attempt(1)]),
            _recorded("mark_in_progress", "done"),
            _recorded("worktree", "done"),
            _recorded("plan_check", "done"),
        ],
    )

    assert cli.interrupted_phase(subtask, _task_workflow()) == "plan_check"


def test_a_run_that_only_lost_its_last_phase_restarts_there_and_not_at_plan_check():
    """The skip-aware branch must stay bounded by the `skip_to` target: with
    `implement`, `review` and `verify` all recorded `done`, no jump can explain a
    missing `mark_done`, and re-running the whole tail would be a fresh run."""
    workflow = _task_workflow()
    subtask = _pure_subtask(
        "card-1",
        [_recorded(name, "done") for name in workflow.phase_names if name != "mark_done"],
    )

    assert cli.interrupted_phase(subtask, workflow) == "mark_done"


def test_a_subtask_with_every_phase_done_has_no_phase_to_resume():
    """The condition `resume_run` turns into `NotResumableError`: only the final
    status write was lost, and re-running `mark_done` would not be a resume."""
    workflow = _task_workflow()
    subtask = _pure_subtask(
        "card-1", [_recorded(name, "done") for name in workflow.phase_names]
    )

    assert cli.interrupted_phase(subtask, workflow) is None


def test_an_interrupted_spec_backs_off_to_the_phase_whose_result_it_binds():
    """`spec` declares `inputs: [card, explore]`, and `explore`'s result lives
    only in the in-memory binding table `run_subtask` builds -- so a walk started
    at `spec` could not render its prompt at all."""
    assert cli.resume_start_phase(_task_workflow(), "spec") == "explore"


def test_an_interrupted_implement_stays_at_implement():
    """`implement` declares `[plan_path, spec_path, branch, base_branch]`, all of
    which `subtask_context` and `_document_paths` supply from the record."""
    assert cli.resume_start_phase(_task_workflow(), "implement") == "implement"


def test_a_deterministic_phase_killed_mid_suite_restarts_at_itself_with_no_orphans():
    """Review Focus: the process died inside `verify.run_suite`. A deterministic
    phase dispatches nothing, so there is no attempt to discard, and
    `verify.run_suite` is read-only -- re-running it is the whole recovery."""
    workflow = _task_workflow()
    subtask = _pure_subtask(
        "card-1",
        [
            *[_recorded(name, "done") for name in workflow.phase_names[:10]],
            _recorded("verify", "started"),
        ],
    )

    assert cli.interrupted_phase(subtask, workflow) == "verify"
    assert cli.resume_start_phase(workflow, "verify") == "verify"
    assert cli.orphan_attempts(subtask) == []


def test_orphan_attempts_are_exactly_the_ones_recorded_started():
    """`started` with no terminal event is §9's in-flight attempt. A
    `gate_failed` or `harness_error` attempt is finished history and must not be
    rewritten."""
    orphan_spec = _pure_attempt(1, status="started")
    orphan_implement = _pure_attempt(2, status="started")
    subtask = _pure_subtask(
        "card-1",
        [
            _recorded(
                "explore",
                "done",
                [_pure_attempt(1, status="gate_failed"), _pure_attempt(2)],
            ),
            _recorded("spec", "started", [orphan_spec]),
            _recorded(
                "implement",
                "started",
                [_pure_attempt(1, status="harness_error"), orphan_implement],
            ),
        ],
    )

    assert cli.orphan_attempts(subtask) == [
        (subtask.phases[1], orphan_spec),
        (subtask.phases[2], orphan_implement),
    ]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k "interrupted or restarts or resume_start or orphan or every_phase_done" -v`
Expected: FAIL — `AttributeError: module 'agent_manager.cli' has no attribute 'interrupted_phase'`.

- [ ] **Step 3: Implement the three helpers**

In `src/agent_manager/cli.py`, immediately after `select_resumable` (Task 1):

```python
def _skipped_origin(
    workflow: Workflow, recorded: Mapping[str, models.PhaseRun], index: int
) -> str | None:
    """The earlier `skip_to` phase whose jump explains an unrecorded phase.

    A skipped phase leaves no row at all (engine.py:431-433 moves the index and
    only appends to the in-memory `summary.skipped`), so "not recorded" reads
    the same as "never reached". The jump is the explanation only when the whole
    stretch between the jumping phase and its target is unrecorded: one recorded
    phase in there proves the walk went through rather than over it.

    The `skip_to` phase is returned rather than its target so the document's own
    `when` decides the jump again -- `plan_check.find_validated_plan` is a
    read-only directory listing, and re-authoring a spec over a plan Validate
    already signed is the outcome this exists to prevent.
    """
    for candidate_index, candidate in enumerate(workflow.phases[:index]):
        if candidate.skip_to is None:
            continue
        target_index = workflow.phase_names.index(candidate.skip_to)
        if not candidate_index < index < target_index:
            continue
        stretch = workflow.phase_names[candidate_index + 1 : target_index]
        if all(name not in recorded for name in stretch):
            return candidate.name
    return None


def interrupted_phase(subtask: models.SubtaskRun, workflow: Workflow) -> str | None:
    """The phase §9's resume re-runs from the top, or `None` if there is none.

    Pure over the recorded tree plus the document, so the choice is testable
    without a store. Two readings of a killed process, in order:

    a phase recorded `started` is the crash signature §9 names -- the manager
    died while that phase was in flight -- and the first such phase wins;
    otherwise the process died between phases and the first phase not recorded
    `done` is the one that never ran, corrected by `_skipped_origin` for the
    stretch a `skip_to` jumped over.

    `None` means every phase of the document is `done`: only the final status
    write was lost, and `resume_run` refuses rather than re-running `mark_done`.
    """
    recorded = {phase.name: phase for phase in subtask.phases}
    for name in workflow.phase_names:
        phase = recorded.get(name)
        if phase is not None and phase.status == "started":
            return name
    for index, phase in enumerate(workflow.phases):
        record = recorded.get(phase.name)
        if record is not None and record.status == "done":
            continue
        origin = _skipped_origin(workflow, recorded, index)
        return phase.name if origin is None else origin
    return None


def resume_start_phase(workflow: Workflow, phase_name: str) -> str:
    """`phase_name`, backed off over the earlier phases whose results it binds.

    A phase's declared `inputs` are resolved out of the binding table
    `engine.run_subtask` builds in memory (`_bind_result`, engine.py:439); the
    journal never replays it. So an input naming an earlier phase is a hard
    dependency on that phase having run *in this process*, and starting past it
    would fail in `prompt.render_prompt` before a single token was billed.

    Only names that are phases of this document count. `card`, `branch`,
    `spec_path` and the rest come from `subtask_context` / `_document_paths` /
    `gate_context` and are supplied on every walk, so `RESERVED_CONTEXT_KEYS` is
    excluded by name -- `_bind_result` skips writing those back anyway, which
    means a same-named phase's result is never what a later phase reads.

    Transitive by construction, and terminating: each hop moves strictly earlier
    in `phase_names`. In `builtin/task.yaml` the only edge is `spec` -> `explore`.
    """
    order = {name: index for index, name in enumerate(workflow.phase_names)}
    current = phase_name
    while True:
        phase = workflow.phase(current)
        producers = [
            name
            for name in getattr(phase, "inputs", ())
            if name in order
            and name not in engine.RESERVED_CONTEXT_KEYS
            and order[name] < order[current]
        ]
        if not producers:
            return current
        current = min(producers, key=lambda name: order[name])


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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k "interrupted or restarts or resume_start or orphan or every_phase_done" -v`
Expected: PASS (9 tests).

- [ ] **Step 5: Run the whole suite**

Run: `uv run pytest`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "feat(cli): choose the restart phase and the orphan attempts of a killed run"
```

---

### Task 3: `resume_run`

**Files:**
- Modify: `src/agent_manager/cli.py` (add `resume_run` at the end of the file, after the `logs` command at cli.py:745-768)
- Test: `tests/test_cli.py` (Engine tier on Steps-tier fixtures, appended after `test_logs_writes_nothing`, test_cli.py:1883)

**Interfaces:**
- Consumes: `cli.select_resumable`, `cli.interrupted_phase`, `cli.resume_start_phase`, `cli.orphan_attempts`, `cli.NotResumableError` (Tasks 1-2); existing `resolve_repo_dir`, `UnknownRunError`, `RunnerFactory`, `default_runner_factory`, `gate_context`, `_utcnow`; `store_module.open_db` / `load_run` / `Store.open`; `board.show`; `load_builtin`; `engine.run_subtask(..., start_phase=..., clock=...)`.
- Produces: `cli.resume_run(run_id: str, *, repo_dir: Path, allow_no_verification: bool = False, commands: Sequence[str] = (), runner_factory: RunnerFactory | None = None, clock: Callable[[], datetime] = _utcnow) -> dict[str, Any]`, returning `run_card`'s payload keys plus `resumed_from: str` and `discarded_attempts: list[dict[str, Any]]`.
- Produces (test helpers Task 4 reuses): `recording_runner(...)`, `_resume_factory(...)`, `_crash_mid_phase(project, cards, phase) -> str`, `_record_interrupted(...)`, `CRASHED_AT`.

- [ ] **Step 1: Write the failing crash-and-resume test**

Append to `tests/test_cli.py`, after `test_logs_writes_nothing` (the last test in the file):

```python
CRASHED_AT = datetime(2026, 9, 23, 11, 30, 0, tzinfo=timezone.utc)
"""The clock `_crash_mid_phase` injects, so the run id is known without reading
a payload the crash never produced."""


def recording_runner(
    *,
    store,
    run_id: str,
    story_id: str,
    card_id: str,
    crash_at: str | None = None,
    seen: list[str] | None = None,
):
    """A fake `engine.AgentPhaseRunner` that writes the rows a real one writes.

    §14's Engine tier: no adapter, no launcher, no harness process. It does
    record what `dispatch.AgentRunner` records -- the phase `started`, then an
    attempt `started` before the dispatch, then the terminal pair -- and numbers
    its attempt directories with the real `dispatch.next_attempt`, because that
    ordering is exactly what `resume` has to find and repair.

    `crash_at` raises `KeyboardInterrupt` in the window between the two writes: a
    `BaseException`, so it escapes `engine.run_subtask`'s `except Exception` the
    way `kill -INT` escapes it, leaving subtask, phase and attempt all `started`.
    """

    def runner(phase, context, rendered):
        if seen is not None:
            seen.append(phase.name)
        n = dispatch.next_attempt(run_id, card_id, phase.name)
        directory = paths.attempt_dir(run_id, card_id, phase.name, n)
        prompt_path = directory / "prompt.txt"
        prompt_path.write_text(rendered.text, encoding="utf-8")
        attempt = models.Attempt(
            n=n,
            dispatch=models.Dispatch(
                harness="fake",
                model="fake",
                role=phase.role,
                cwd=Path(context["worktree"]),
                prompt_path=prompt_path,
                result_path=directory / "result.json",
            ),
            status="started",
            prompt_path=prompt_path,
        )
        store.record_phase(
            story_id, card_id, models.PhaseRun(name=phase.name, kind="agent", status="started")
        )
        store.record_attempt(story_id, card_id, phase.name, attempt)
        if crash_at is not None and phase.name == crash_at:
            raise KeyboardInterrupt(f"simulated kill during {phase.name}")
        store.record_attempt(
            story_id,
            card_id,
            phase.name,
            attempt.model_copy(update={"status": "ok", "exit_code": 0}),
        )
        store.record_phase(
            story_id, card_id, models.PhaseRun(name=phase.name, kind="agent", status="done")
        )
        if phase.name == "explore":
            return dict(EXPLORE_RESULT)
        return {"phase": phase.name, "ok": True}

    return runner


def _resume_factory(seen: list[str] | None = None, crash_at: str | None = None):
    """A `cli.RunnerFactory` handing `recording_runner` the store the CLI opened."""

    def factory(*, workflow, store, run_id, story_id, card_id):
        return recording_runner(
            store=store,
            run_id=run_id,
            story_id=story_id,
            card_id=card_id,
            crash_at=crash_at,
            seen=seen,
        )

    return factory


def _crash_mid_phase(project: Path, cards: dict[str, str], phase: str) -> str:
    """Drive a real `run_card` until it is killed inside `phase`, and name the run."""
    run_id = cli.mint_run_id(cards["subtask"], CRASHED_AT)
    with pytest.raises(KeyboardInterrupt):
        cli.run_card(
            cards["subtask"],
            repo_dir=project,
            base_branch="main",
            clock=lambda: CRASHED_AT,
            runner_factory=_resume_factory(crash_at=phase),
        )
    return run_id


@requires_git
@requires_brd
def test_a_run_killed_mid_implement_resumes_and_leaves_no_started_attempt(project, cards):
    """§14's required test: a simulated crash mid-phase, then a resume.

    The crash leaves the subtask, the phase and the attempt all recorded
    `started`; §9 says resume discards the in-flight attempt and re-runs that
    phase from the top. An attempt row left `started` after the run finished is
    the corruption this whole command exists to prevent.
    """
    run_id = _crash_mid_phase(project, cards, "implement")
    assert [
        row for row in _attempt_rows(project) if row[3] == "implement" and row[5] == "started"
    ] != []

    payload = cli.resume_run(run_id, repo_dir=project, runner_factory=_resume_factory())

    assert payload["status"] == "done"
    assert payload["resumed_from"] == "implement"
    assert {"phase": "implement", "n": 1} in payload["discarded_attempts"]
    assert payload["run_id"] == run_id
    assert payload["card_id"] == cards["subtask"]
    assert payload["story_id"] == cards["story"]
    assert [row for row in _attempt_rows(project) if row[5] == "started"] == []
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_cli.py::test_a_run_killed_mid_implement_resumes_and_leaves_no_started_attempt -v`
Expected: FAIL — `AttributeError: module 'agent_manager.cli' has no attribute 'resume_run'`.

- [ ] **Step 3: Implement `resume_run`**

Append to `src/agent_manager/cli.py`, after the `logs` command:

```python
def resume_run(
    run_id: str,
    *,
    repo_dir: Path,
    allow_no_verification: bool = False,
    commands: Sequence[str] = (),
    runner_factory: RunnerFactory | None = None,
    clock: Callable[[], datetime] = _utcnow,
) -> dict[str, Any]:
    """Pick one killed run back up at the phase it died in (§9 lines 370-386).

    The order is the spec's and it is load-bearing in the same way `run_card`'s
    is, only inverted: every refusal -- unknown run, nothing in flight, a card
    the board lost, a workflow that will not load -- happens before `Store.open`,
    because `Store.open` constructs a `Journal` and therefore mints a run
    directory, and a refusal that left one behind would be this command writing
    state for a run it declined to touch.

    Branch, base branch and worktree come from the recorded `SubtaskRun` and
    never from a flag: §9's "the run records what it was started with" is the
    reason the record exists. The two knobs the record does *not* carry --
    `models.RunConfig` has no suite commands and no `allow_no_verification` --
    are taken as arguments here rather than grown onto the model, so a resume
    means exactly what a fresh `run` with the same flags means.
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

    story, subtask = select_resumable(run)
    card = board.show(subtask.card_id, repo_dir=root)
    parent = board.show(story.card_id, repo_dir=root)
    workflow = load_builtin(run.workflow)
    interrupted = interrupted_phase(subtask, workflow)
    if interrupted is None:
        raise NotResumableError(
            f"every phase of card {subtask.card_id} in run {run.id!r} is recorded"
            " 'done', so there is no phase to re-run -- only the final status write"
            " was lost; start a fresh run with `agent-manager run --card` if the card"
            " still needs work"
        )
    start_phase = resume_start_phase(workflow, interrupted)
    orphans = orphan_attempts(subtask)
    resumed = subtask.model_copy(update={"status": "started"})

    store = Store.open(root, run.id)
    try:
        # Journal first, row after -- `record_attempt`'s own ordering, and the
        # reason no delete path is needed: the orphan is one more `attempt_upsert`
        # keyed by (phase, n), so `replay` and `rebuild_from_journal` need to know
        # nothing about resume. The attempt *directory* is left alone: its prompt
        # and stdout are the only evidence of what the killed process was doing.
        for phase, attempt in orphans:
            store.record_attempt(
                story.card_id,
                subtask.card_id,
                phase.name,
                attempt.model_copy(update={"status": "harness_error"}),
            )
        store.record_run(run.model_copy(update={"status": "started"}))
        store.record_story(story.model_copy(update={"status": "started"}))
        store.record_subtask(story.card_id, resumed)

        factory = default_runner_factory if runner_factory is None else runner_factory
        runner = factory(
            workflow=workflow,
            store=store,
            run_id=run.id,
            story_id=story.card_id,
            card_id=subtask.card_id,
        )
        summary = engine.run_subtask(
            workflow,
            store,
            story_id=story.card_id,
            subtask=resumed,
            repo_dir=root,
            commands=commands,
            card=card,
            parent_story=parent,
            extra_context=gate_context(commands, allow_no_verification),
            agent_runner=runner,
            start_phase=start_phase,
            clock=clock,
        )

        store.record_run(run.model_copy(update={"status": summary.status}))
        store.record_story(story.model_copy(update={"status": summary.status}))
        store.record_subtask(
            story.card_id, resumed.model_copy(update={"status": summary.status})
        )

        warnings = list(summary.warnings) + list(getattr(runner, "warnings", []))
        return {
            "run_id": run.id,
            "card_id": subtask.card_id,
            "story_id": story.card_id,
            "branch": subtask.branch,
            "base_branch": subtask.base_branch,
            "worktree": None
            if subtask.worktree_path is None
            else str(subtask.worktree_path),
            "status": summary.status,
            "failed_phase": summary.failed_phase,
            "detail": summary.detail,
            "skipped": list(summary.skipped),
            "warnings": warnings,
            "resumed_from": start_phase,
            "discarded_attempts": [
                {"phase": phase.name, "n": attempt.n} for phase, attempt in orphans
            ],
        }
    finally:
        store.close()
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_cli.py::test_a_run_killed_mid_implement_resumes_and_leaves_no_started_attempt -v`
Expected: PASS.

- [ ] **Step 5: Write the failing attempt-numbering test**

Append to `tests/test_cli.py`:

```python
@requires_git
@requires_brd
def test_the_resumed_dispatch_numbers_past_the_attempt_the_crash_left(project, cards):
    """`dispatch.next_attempt` scans directories on disk precisely so a resumed
    run cannot overwrite the prompt and log of the attempt that died -- they are
    the only record of what the killed process was doing."""
    run_id = _crash_mid_phase(project, cards, "implement")
    crashed_dir = paths.run_dir(run_id) / cards["subtask"] / "implement.1"
    prompt_before = (crashed_dir / "prompt.txt").read_bytes()

    cli.resume_run(run_id, repo_dir=project, runner_factory=_resume_factory())

    assert (crashed_dir / "prompt.txt").read_bytes() == prompt_before
    assert (paths.run_dir(run_id) / cards["subtask"] / "implement.2").is_dir()
    implement = [row for row in _attempt_rows(project) if row[3] == "implement"]
    assert [(row[4], row[5]) for row in implement] == [(1, "harness_error"), (2, "ok")]
```

- [ ] **Step 6: Run it**

Run: `uv run pytest tests/test_cli.py::test_the_resumed_dispatch_numbers_past_the_attempt_the_crash_left -v`
Expected: PASS — `resume_run` reuses the run id, so `next_attempt` and the journal both continue. If it fails with `(1, "ok")`, `resume_run` minted a new run id instead of reopening `run.id`.

- [ ] **Step 7: Write the no-harness and back-off tests**

Append to `tests/test_cli.py`:

```python
@requires_git
@requires_brd
def test_resume_launches_no_harness(project, cards, monkeypatch):
    """§14's adapter rule at the resume seam: the launcher is injected, so a
    resume that got as far as launching one has already failed."""
    run_id = _crash_mid_phase(project, cards, "implement")

    def forbidden(*args, **kwargs):
        raise AssertionError("resume launched a harness process")

    monkeypatch.setattr(cli, "run_direct", forbidden)
    monkeypatch.setattr(cli.dispatch, "AgentRunner", forbidden)

    payload = cli.resume_run(run_id, repo_dir=project, runner_factory=_resume_factory())

    assert payload["status"] == "done"


@requires_git
@requires_brd
def test_a_run_killed_in_spec_restarts_at_explore_so_specs_input_is_bound(project, cards):
    """The back-off rule observed end to end: `spec` declares `explore` as an
    input, `explore`'s result was only ever in the dead process's memory, so the
    resumed walk has to produce it again before `spec` can render at all."""
    run_id = _crash_mid_phase(project, cards, "spec")
    seen: list[str] = []

    payload = cli.resume_run(run_id, repo_dir=project, runner_factory=_resume_factory(seen))

    assert payload["resumed_from"] == "explore"
    assert payload["status"] == "done"
    assert seen[0] == "explore"
    assert seen.index("explore") < seen.index("spec")


@requires_git
@requires_brd
def test_resume_passes_its_own_allow_no_verification_into_the_gate_context(project, cards):
    """`RunConfig` records neither the suite commands nor §12's opt-out, so the
    flag is the command's own -- and it has to reach the same four context keys
    `run_card` supplies (cli.py:438-455)."""
    run_id = _crash_mid_phase(project, cards, "spec")
    contexts: list[dict[str, Any]] = []

    def factory(*, workflow, store, run_id, story_id, card_id):
        def collect(phase, context, rendered):
            contexts.append(dict(context))
            if phase.name == "explore":
                return dict(EXPLORE_RESULT)
            return {"phase": phase.name, "ok": True}

        return collect

    cli.resume_run(
        run_id, repo_dir=project, allow_no_verification=True, runner_factory=factory
    )

    assert contexts[0]["allow_no_verification"] is True
    assert contexts[0]["suite_cmds"] == []
    assert contexts[0]["caller_provided"] is False
    assert contexts[0]["provided_verification"] is None
```

- [ ] **Step 8: Run them**

Run: `uv run pytest tests/test_cli.py -k "resume_launches_no_harness or killed_in_spec or allow_no_verification_into_the_gate" -v`
Expected: PASS (3 tests).

- [ ] **Step 9: Write the failing plan_check Review Focus test**

Append to `tests/test_cli.py`:

```python
def _record_interrupted(
    project: Path,
    cards: dict[str, str],
    run_id: str,
    *,
    workflow: str = "task",
    done: tuple[str, ...] = (),
    started: str | None = None,
) -> None:
    """A run the projection holds as killed in flight, written row by row.

    The board and the git repo stay the `project` fixture's real ones, so
    `resume` can re-fetch the cards; only the run state is hand-built, because
    the two states these tests need -- a stretch a `skip_to` jumped over, and a
    workflow name no builtin matches -- are not states `run_card` can be driven
    into. Written through `Store`, which is the only way this program writes
    rows.
    """
    branch = dag.task_branch("m1", board.show(cards["subtask"], repo_dir=project))
    opened = store_module.Store.open(project, run_id)
    try:
        opened.record_run(
            models.Run(
                id=run_id,
                workflow=workflow,
                repo_dir=project,
                base_branch="main",
                branch_prefix="m1",
                status="started",
                started_at=CRASHED_AT,
            )
        )
        opened.record_story(
            models.StoryRun(
                card_id=cards["story"],
                title="The CLI",
                level=0,
                status="started",
                tip_branch=branch,
            )
        )
        opened.record_subtask(
            cards["story"],
            models.SubtaskRun(
                card_id=cards["subtask"],
                branch=branch,
                base_branch="main",
                status="started",
                worktree_path=cli.worktree_for(project, branch),
            ),
        )
        for name in done:
            opened.record_phase(cards["story"], cards["subtask"], _recorded(name, "done"))
        if started is not None:
            opened.record_phase(
                cards["story"], cards["subtask"], _recorded(started, "started")
            )
    finally:
        opened.close()


@requires_git
@requires_brd
def test_a_restart_at_plan_check_that_finds_no_plan_is_an_engine_error_not_a_traceback(
    project, cards
):
    """Review Focus: restarting at `plan_check` re-asks its `when`, and a `when`
    that now says "no validated plan" drops the walk into `spec`, whose `explore`
    input no fresh context supplies. The honest outcome is the engine's own
    refusal naming the input -- which `HANDLED` turns into an envelope at exit 3
    -- and never an unhandled traceback."""
    run_id = cli.mint_run_id(cards["subtask"], CRASHED_AT)
    _record_interrupted(
        project,
        cards,
        run_id,
        done=("explore", "mark_in_progress", "worktree", "plan_check"),
    )

    with pytest.raises(EngineError) as caught:
        cli.resume_run(run_id, repo_dir=project, runner_factory=_resume_factory())

    assert "explore" in str(caught.value)
    assert isinstance(caught.value, cli.HANDLED)
```

- [ ] **Step 10: Run it**

Run: `uv run pytest tests/test_cli.py::test_a_restart_at_plan_check_that_finds_no_plan_is_an_engine_error_not_a_traceback -v`
Expected: PASS.

- [ ] **Step 11: Run the whole suite**

Run: `uv run pytest`
Expected: PASS.

- [ ] **Step 12: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "feat(cli): resume_run reopens a killed run and re-runs its interrupted phase"
```

---

### Task 4: The `resume` command

**Files:**
- Modify: `src/agent_manager/cli.py` (add the `resume` Typer command at the end of the file, after `resume_run`)
- Test: `tests/test_cli.py` (CLI tier via `runner.invoke`, appended after Task 3's tests)

**Interfaces:**
- Consumes: `cli.resume_run` (Task 3); `HANDLED`, `EXIT_ERROR`, `EXIT_ESCALATED`, `render`, `ok_envelope`, `error_envelope`; test helpers `runner`, `_record`, `RECORDED_AT`, `projection`, `_runs_snapshot`, `_attempt_rows`, `fake_runner`, `_crash_mid_phase`, `_record_interrupted`, `CRASHED_AT`.
- Produces: the `resume` subcommand — `agent-manager resume RUN_ID [--repo-dir PATH] [--allow-no-verification] [--pretty]`.

- [ ] **Step 1: Write the failing refusal tests**

Append to `tests/test_cli.py`, after Task 3's tests:

```python
def test_resume_of_an_unknown_run_is_an_envelope(projection):
    result = runner.invoke(
        cli.app, ["resume", "no-such-run", "--repo-dir", str(projection)]
    )

    assert result.exit_code == cli.EXIT_ERROR
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is False
    assert envelope["error"]["type"] == "UnknownRunError"
    assert "no-such-run" in envelope["error"]["message"]


@pytest.mark.parametrize("status", ["done", "escalated"])
def test_resume_of_a_run_with_nothing_in_flight_is_an_envelope(projection, status):
    """A finished run has nothing to pick up and an escalated one is `retry`'s
    job; both are the same refusal, and the message is what tells them apart."""
    _record(projection, "20260923T090000Z-cbe34d00", started_at=RECORDED_AT, status=status)

    result = runner.invoke(
        cli.app, ["resume", "20260923T090000Z-cbe34d00", "--repo-dir", str(projection)]
    )

    assert result.exit_code == cli.EXIT_ERROR
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is False
    assert envelope["error"]["type"] == "NotResumableError"
    assert status in envelope["error"]["message"]


def test_resume_with_a_repo_dir_that_is_not_a_directory_is_an_envelope(tmp_path, monkeypatch):
    """Review Focus: `--repo-dir` is refused before any projection is opened, the
    same way it is for every other command."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))

    result = runner.invoke(
        cli.app, ["resume", "any-run", "--repo-dir", str(tmp_path / "missing")]
    )

    assert result.exit_code == cli.EXIT_ERROR
    envelope = json.loads(result.stdout)
    assert envelope["error"]["type"] == "RepoDirError"
    assert "missing" in envelope["error"]["message"]


def test_resume_writes_nothing_when_it_refuses(projection):
    """§9's refusal rule: `Store.open` constructs a `Journal` and mints a run
    directory, so a command that declined to resume must never have reached it."""
    _record(projection, "20260923T090000Z-cbe34d00", started_at=RECORDED_AT)
    tree_before = _runs_snapshot()
    rows_before = _attempt_rows(projection)

    refusal = runner.invoke(
        cli.app, ["resume", "no-such-run", "--repo-dir", str(projection)]
    )

    assert refusal.exit_code == cli.EXIT_ERROR
    assert not (paths.data_dir() / "runs" / "no-such-run").exists()
    assert _runs_snapshot() == tree_before
    assert _attempt_rows(projection) == rows_before
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_cli.py -k "resume_of_an_unknown_run or nothing_in_flight or resume_with_a_repo_dir or resume_writes_nothing" -v`
Expected: FAIL with exit code 2 and Typer's `No such command 'resume'` usage error.

- [ ] **Step 3: Add the command**

Append to `src/agent_manager/cli.py`, after `resume_run`:

```python
@app.command("resume")
def resume(
    run_id: str = typer.Argument(..., metavar="RUN_ID", help="The run to pick back up."),
    repo_dir: Path = typer.Option(
        Path("."), "--repo-dir", help="The repository and brd board to work in."
    ),
    allow_no_verification: bool = typer.Option(
        False,
        "--allow-no-verification",
        help="Proceed even when no verification suite is available (§12's opt-out).",
    ),
    pretty: bool = typer.Option(False, "--pretty", help="Indent the JSON envelope."),
) -> None:
    """Re-run the phase a killed run died in, and drive the subtask to the end.

    No `--base-branch` and no `--branch-prefix`: both were decided when the run
    started and are recorded on the subtask (§9). `--allow-no-verification` is
    offered because `models.RunConfig` does not carry it, so the flag means the
    same thing here as it does on a fresh `run`.
    """
    try:
        payload = resume_run(
            run_id,
            repo_dir=repo_dir,
            allow_no_verification=allow_no_verification,
        )
    except HANDLED as error:
        typer.echo(render(error_envelope(error), pretty=pretty))
        raise typer.Exit(EXIT_ERROR) from None
    typer.echo(render(ok_envelope(payload), pretty=pretty))
    if payload["status"] == "escalated":
        raise typer.Exit(EXIT_ESCALATED)
```

- [ ] **Step 4: Run the refusal tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k "resume_of_an_unknown_run or nothing_in_flight or resume_with_a_repo_dir or resume_writes_nothing" -v`
Expected: PASS (5 tests — the `nothing_in_flight` one is parametrized).

- [ ] **Step 5: Write the failing success-path and escalation tests**

Append to `tests/test_cli.py`:

```python
@requires_git
@requires_brd
def test_the_resume_command_prints_an_ok_envelope_and_exits_zero(project, cards, monkeypatch):
    """The factory is patched on the module rather than passed as an option: the
    injection seam is `cli.default_runner_factory`, and patching it is what
    proves the command reaches for that name."""
    run_id = _crash_mid_phase(project, cards, "implement")
    monkeypatch.setattr(cli, "default_runner_factory", lambda **kwargs: fake_runner())

    result = runner.invoke(cli.app, ["resume", run_id, "--repo-dir", str(project)])

    assert result.exit_code == 0
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is True
    assert envelope["data"]["status"] == "done"
    assert envelope["data"]["resumed_from"] == "implement"
    assert envelope["data"]["discarded_attempts"] == [{"phase": "implement", "n": 1}]
    assert "\n" not in result.stdout.strip()


@requires_git
@requires_brd
def test_resume_pretty_indents_the_same_envelope(project, cards, monkeypatch):
    run_id = _crash_mid_phase(project, cards, "implement")
    monkeypatch.setattr(cli, "default_runner_factory", lambda **kwargs: fake_runner())

    result = runner.invoke(
        cli.app, ["resume", run_id, "--repo-dir", str(project), "--pretty"]
    )

    assert result.exit_code == 0
    assert "\n" in result.stdout.strip()
    assert json.loads(result.stdout)["data"]["status"] == "done"


@requires_git
@requires_brd
def test_a_resumed_walk_that_escalates_is_ok_true_and_exit_one(project, cards, monkeypatch):
    """An escalation is a truthful result, so the envelope stays `ok: true` and
    the exit code carries the full stop -- exactly as `run` does."""
    run_id = _crash_mid_phase(project, cards, "implement")
    monkeypatch.setattr(
        cli, "default_runner_factory", lambda **kwargs: fake_runner(fail="review")
    )

    result = runner.invoke(cli.app, ["resume", run_id, "--repo-dir", str(project)])

    assert result.exit_code == cli.EXIT_ESCALATED
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is True
    assert envelope["data"]["status"] == "escalated"
    assert envelope["data"]["failed_phase"] == "review"
    assert envelope["data"]["resumed_from"] == "implement"


@requires_git
@requires_brd
def test_a_run_recorded_with_an_unknown_workflow_is_an_envelope(project, cards, monkeypatch):
    """Review Focus: the workflow name comes off the record, and a projection
    row naming a document no builtin matches has to reach the operator as the
    loader's own refusal rather than as a traceback."""
    run_id = cli.mint_run_id(cards["subtask"], CRASHED_AT)
    _record_interrupted(project, cards, run_id, workflow="nope", started="implement")
    monkeypatch.setattr(cli, "default_runner_factory", lambda **kwargs: fake_runner())

    result = runner.invoke(cli.app, ["resume", run_id, "--repo-dir", str(project)])

    assert result.exit_code == cli.EXIT_ERROR
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is False
    assert envelope["error"]["type"] == "WorkflowLoadError"
```

- [ ] **Step 6: Run them**

Run: `uv run pytest tests/test_cli.py -k "resume_command_prints or resume_pretty or resumed_walk_that_escalates or unknown_workflow" -v`
Expected: PASS (4 tests).

- [ ] **Step 7: Update the test module docstring**

`tests/test_cli.py` opens (lines 1-2) with:

```python
"""Behaviour of the `run --card` command (design §10, spec card cbe34d00).
```

Replace that first line with:

```python
"""Behaviour of the `run`, `status`, `runs`, `logs` and `resume` commands (§10).
```

- [ ] **Step 8: Run the whole suite**

Run: `uv run pytest`
Expected: PASS. No test of `run`, `status`, `runs` or `logs` changed behaviour.

- [ ] **Step 9: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "feat(cli): add the resume command (§10 grammar, brd envelope, exit codes)"
```
