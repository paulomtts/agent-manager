<!-- task-pipeline: validated -->
# Let the engine stop between phases on request (card 0d8b7c9a)

Subtask of story 9bfb5ac2 "Stop cleanly: a stopped status and a cooperative stop" (addendum decision P4, `docs/superpowers/specs/2026-09-24-parallel-stories-design.md` lines 66-75). This narrows P4 to the engine's side of the stop: a check before each phase. It extends `2026-09-24-orchestration-design.md` and `2026-09-23-agent-manager-design.md` and changes nothing they decided.

## Precondition

Sibling a3dd82f4 "Add the stopped status" must be present. This worktree already has it: `models.Status` includes `"stopped"` (models.py:25) and `SubtaskSummary.status` is `Literal["done", "escalated", "stopped"]` (engine.py:244). `master` does not; if this card is ever rebased onto a base without that work, stop and merge the sibling branch first rather than re-adding `stopped` here. The `am status` rows, `select_resumable` refusal text, exit code and envelope handling of `stopped`, `rebuild_from_journal` and their round-trip tests belong to that sibling and are not touched.

## Scope

1. `engine.run_subtask` gains a keyword `should_stop: Callable[[], bool] | None = None`.
2. At the top of every iteration of the phase loop (`while index < len(workflow.phases)`, engine.py:385), before anything about the phase runs, the engine calls `should_stop()` if it is not None. The check comes before the `agent_runner is None` `EngineError`, before `prompt.render_prompt`, and before `_run_deterministic` records the phase `started`. So it applies to deterministic and agent phases alike, and nothing about the phase runs.
3. When the check returns true, the engine records the subtask `stopped` via `_record_subtask_status(store, story_id, subtask, "stopped")`, which writes both the store row and the journal line through `store.record_subtask`. It sets `summary.status = "stopped"` and `summary.detail = "stopped before <phase.name>"`, leaves `summary.failed_phase` as `None`, and returns the summary immediately. Results, warnings and skipped phases gathered so far stay on the summary, as with `_escalate`. A small helper next to `_escalate` is fine, but it must not share `_escalate`'s `failed_phase` assignment: `stopped` is not `failed`.
4. The engine never checks during a phase. A phase already started, including an agent phase in flight, finishes and is recorded normally (done, failed or escalated). A stop that turns true during phase N shows up at the check before phase N+1. If phase N escalates, the escalation wins and there is no later check. If the walk finishes all phases, the subtask is recorded `done` as today, with no check after the last phase.
5. The engine checks only the loop indices it actually visits. Phases jumped over by a `when` `skip_to` are never visited, so they are never checked.
6. `cli.drive_subtask` (cli.py:679) gains an optional keyword `should_stop: Callable[[], bool] | None = None` and passes it straight to `engine.run_subtask`. `orchestrate.Driver` (orchestrate.py:46-66) gains the same keyword with default `None`, so `cli.drive_subtask` still satisfies the protocol.
7. Existing callers keep their current code. `orchestrate.run_milestone`'s `drive(...)` call (orchestrate.py:301), `run_card`, `resume`, and every fake driver in the tests stay as they are and pass no `should_stop`. Wiring a real shared `threading.Event` into those callers is other stories' work (P5/P6/P7). So is handling a `stopped` summary in orchestrate's `status != "done"` branch (orchestrate.py:322-331), which cannot happen until someone passes `should_stop`.

## Observable behaviour

- `should_stop=None`, or a callable that never returns true, gives exactly today's behaviour. That means the same phases, store rows, journal lines and summary. `--max-concurrent 1` and the sequential runner are unaffected.
- On a stop, the subtask row and its journal record read `stopped`. The summary reads `status="stopped"`, `failed_phase=None`, `detail="stopped before <name>"`. The engine starts no later phase: no `started` phase row, no agent-runner call, and no deterministic function call.
- Re-entry: the store has no CHECK on status, so there is no schema change. A `stopped` subtask can be driven again by a fresh `run_subtask` call over the same store. It continues through the same idempotence, `start_phase`, and Plan-Hash re-entrancy that resume already relies on, and it finishes `done`.

## Error paths

- `should_stop` raising is not caught. It is the caller's own callable (a flag read), and the engine does not turn it into an escalation. It propagates the same way a bad `start_phase` or a reserved `extra_context` key does.
- An agent phase with no injected runner still raises `EngineError`, but only if the walk actually reaches that phase without stopping first.

## Tests

All engine tests are in the engine tier (design spec section 14: engine driven with a fake runner or canned functions). They go in `tests/test_engine.py` and use its hand-built `FunctionRegistry`, the real temp SQLite and JSONL `store` fixture, and its existing helpers (`_registry`, `_workflow`, `_recording_runner`, `_journalled_phases`, `_projected_phases`, `_journalled_details`). None of these tests touch `tests/e2e` or call a real `claude`, and no new tier is added.

1. Stop during phase 3 (engine tier): `should_stop` becomes true while phase 3 runs (for example, set by phase 3's canned function or runner). Phases 1-3 are recorded as normal. The call log shows no phase 4 or later. The store row and journal show the subtask `stopped`, and the summary reads `status="stopped"`, `failed_phase is None`, `detail == "stopped before <phase 4 name>"`.
2. Already stopped (engine tier): `should_stop` is true from the start. No phase is recorded, no function or runner is called, and the subtask is `stopped` with `detail == "stopped before <phase 1 name>"`.
3. Both phase kinds are checked (engine tier): in a workflow that mixes deterministic and agent phases, a stop set just before a deterministic phase leaves it with no `started` row and its function uncalled. A stop set just before an agent phase leaves the recording runner uncalled. The agent case includes a walk with `agent_runner=None` that stops before its agent phase and returns `stopped` rather than raising `EngineError`.
4. Never fires (engine tier): with a `should_stop` that always returns false, the walk's summary, phase rows and journal match a walk without it. The existing engine suite passes unchanged.
5. Re-drive after a stop (engine tier): a first `run_subtask` stops before phase N. A second `run_subtask` over the same store, with the same subtask and `start_phase=<phase N>` and no stop, runs only phase N onward according to the call log. It finishes with the subtask recorded `done` in both the store row and the journal.
6. Pass-through (CLI tier, `tests/test_cli.py`, only if cheap with the existing temp git/brd fixtures and a fake `runner_factory`): `cli.drive_subtask(..., should_stop=lambda: True)` returns a summary with `status == "stopped"` and never calls the fake runner.
7. Driver compatibility (`tests/test_orchestrate.py`): add this only if the existing orchestrate suite does not already show that fake drivers without a `should_stop` parameter still work. Fake drivers are not modified.

Card rules still hold. Any fake `claude` knows only what the brief says: it does not compute the plan hash, commit the spec or plan, or learn the result path from anything other than the prompt text. The whole default suite, including `tests/e2e`, stays green. Everything runs in one process, with no support for two `am` processes on one repo or run.

---

# Let the engine stop between phases Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `engine.run_subtask` accepts an optional `should_stop` callable, checks it before every visited phase, and records the subtask `stopped` (not `failed`) when it returns true; `cli.drive_subtask` and `orchestrate.Driver` carry the same optional keyword through.

**Architecture:** One check at the top of the phase loop in `src/agent_manager/engine.py`, placed before any per-phase work, plus a small `_stop` helper next to `_escalate` that records `stopped` and returns the summary without touching `failed_phase`. `cli.drive_subtask` forwards the keyword verbatim; `orchestrate.Driver` gains the matching optional parameter so `cli.drive_subtask` still satisfies the protocol. No caller passes it yet.

**Tech Stack:** Python 3, pytest, `uv`. Store is the real temp SQLite projection plus JSONL journal from `tests/test_engine.py`'s `store` fixture.

**Spec:** `docs/superpowers/specs/task-let-the-engine-stop-0d8b7c9a-design.md` (prepended verbatim above).

## Global Constraints

- Precondition: `models.Status` already contains `"stopped"` (src/agent_manager/models.py:25) and `SubtaskSummary.status` is `Literal["done", "escalated", "stopped"]` (src/agent_manager/engine.py:244). This branch is cut from `m4/task-add-the-stopped-status-a3dd82f4`, so both exist. If either is missing, stop and merge that sibling branch; do not add `stopped` in this card.
- Keyword signature, exactly: `should_stop: Callable[[], bool] | None = None`.
- Detail string, exactly: `f"stopped before {phase.name}"`.
- On a stop: `summary.status = "stopped"`, `summary.failed_phase` stays `None`, subtask recorded via `_record_subtask_status(store, story_id, subtask, "stopped")`.
- The check never runs inside a phase and never after the last phase; an exception from `should_stop` is not caught.
- No schema change, no new test tier, nothing under `tests/e2e`, no real `claude`.
- Existing callers (`orchestrate.run_milestone`'s `drive(...)`, `run_card`, `resume`) and every fake driver (`tests/test_orchestrate.py::FakeDriver`) stay unchanged.
- Whole default suite green: `uv run pytest`.

## Review Focus

- `should_stop` raises: the exception propagates out of `run_subtask` and no subtask row is written (a reasonable caller expects its own bug to surface, not a fake escalation). Pinned in Task 1 by `test_an_exception_from_should_stop_propagates_and_records_nothing`.
- Stop flag set during a phase that then escalates: the subtask must read `escalated` with `failed_phase` set, never `stopped`. Pinned in Task 1 by `test_an_escalation_during_the_stop_request_wins_over_the_stop`.
- A `when`/`skip_to` jump: the jumped-over phases must not be checked, and there is no check after the last phase. Pinned in Task 1 by `test_should_stop_is_checked_only_at_visited_phases`.
- An agent phase reached with `agent_runner=None` while a stop is pending: must return `stopped`, not raise `EngineError`. Pinned in Task 1 by `test_a_stop_before_an_agent_phase_wins_over_a_missing_runner`.
- Re-driving a stopped subtask with `start_phase`: must run only the remaining phases and flip the row to `done`. Pinned in Task 1 by `test_a_stopped_subtask_can_be_driven_again_to_done`.

## File Structure

- Modify: `src/agent_manager/engine.py` -- `run_subtask` signature (lines 340-354) and loop head (line 385-386); new `_stop` helper after `_escalate` (after line 575).
- Modify: `src/agent_manager/cli.py` -- `drive_subtask` signature (lines 679-690) and the `engine.run_subtask(...)` call (lines 708-719). `Callable` is already imported at cli.py:21.
- Modify: `src/agent_manager/orchestrate.py` -- `Driver.__call__` (lines 53-65). `Callable` is already imported at orchestrate.py:25.
- Test: `tests/test_engine.py` (engine tier) -- append new tests at end of file.
- Test: `tests/test_cli.py` (CLI tier, real temp git/brd fixtures, fake `runner_factory`) -- append one test after `test_drive_subtask_drives_two_subtasks_under_one_store_and_run` (ends line 1484).
- `tests/test_orchestrate.py` is NOT modified: `orchestrate.Driver` is a plain (not `runtime_checkable`) `Protocol`, and the existing orchestrate suite already drives `run_milestone` through `FakeDriver` (tests/test_orchestrate.py:235), which takes no `should_stop`, via the unchanged `drive(...)` call. That suite passing is the compatibility proof spec item 7 asks for.

---

### Task 1: The engine checks `should_stop` before each visited phase

**Files:**
- Modify: `src/agent_manager/engine.py:340-354` (signature), `:385-386` (loop head), after `:575` (new `_stop`)
- Test: `tests/test_engine.py` (append at end of file, after `test_a_subtask_summary_may_report_stopped`)

**Interfaces:**
- Consumes: `engine._record_subtask_status(store, story_id, subtask, status)` (engine.py:551), `SubtaskSummary` (engine.py:235-249), test helpers `_subtask`, `_workflow`, `_journalled_phases`, `_projected_phases`, `_recording_runner`, `_skipping_workflow`, `THREE_PHASES`, `STORY_ID`, `REPO`, fixture `store` (all in tests/test_engine.py).
- Produces: `engine.run_subtask(..., should_stop: Callable[[], bool] | None = None) -> SubtaskSummary`; on a stop the summary has `status == "stopped"`, `failed_phase is None`, `detail == "stopped before <phase name>"`. Task 2 relies on this exact keyword name.

- [ ] **Step 1: Write the failing engine tests**

Append to the end of `tests/test_engine.py`:

```python
FOUR_PHASES = """
name: four
phases:
  - name: alpha
    kind: deterministic
    run: step.alpha
  - name: beta
    kind: deterministic
    run: step.beta
  - name: gamma
    kind: deterministic
    run: step.gamma
  - name: delta
    kind: deterministic
    run: step.delta
"""

STOP_MIXED = """
name: stop_mixed
phases:
  - name: prepare
    kind: deterministic
    run: step.prepare
  - name: explore
    kind: agent
    role: explorer
    result: ExploreResult
  - name: finish
    kind: deterministic
    run: step.finish
"""


def _subtask_journal_statuses(opened) -> list[str]:
    return [
        line.payload["status"]
        for line in opened.journal.read()
        if line.event == "subtask_upsert"
    ]


def _projected_subtask_status(opened, card: str = "ed77a917") -> str | None:
    row = opened.connection.execute(
        "SELECT status FROM subtasks WHERE card_id = ?", (card,)
    ).fetchone()
    return None if row is None else row[0]


class _StopFlag:
    """A `should_stop` whose answer a canned step flips mid-walk."""

    def __init__(self, value: bool = False) -> None:
        self.value = value

    def set(self) -> None:
        self.value = True

    def __call__(self) -> bool:
        return self.value


def test_a_stop_requested_during_phase_three_stops_before_phase_four(store):
    calls: list[str] = []
    flag = _StopFlag()

    def make(name: str, *, stop: bool = False):
        def step(card: str) -> dict[str, Any]:
            calls.append(name)
            if stop:
                flag.set()
            return {"phase": name}

        return step

    workflow = _workflow(
        FOUR_PHASES,
        {
            "step.alpha": make("alpha"),
            "step.beta": make("beta"),
            "step.gamma": make("gamma", stop=True),
            "step.delta": make("delta"),
        },
    )

    summary = engine.run_subtask(
        workflow,
        store,
        story_id=STORY_ID,
        subtask=_subtask(),
        repo_dir=REPO,
        should_stop=flag,
    )

    assert calls == ["alpha", "beta", "gamma"]
    assert summary.status == "stopped"
    assert summary.failed_phase is None
    assert summary.detail == "stopped before delta"
    assert set(summary.results) == {"alpha", "beta", "gamma"}
    assert _projected_phases(store) == [
        ("alpha", "done"),
        ("beta", "done"),
        ("gamma", "done"),
    ]
    assert ("delta", "started") not in _journalled_phases(store)
    assert _subtask_journal_statuses(store) == ["stopped"]
    assert _projected_subtask_status(store) == "stopped"


def test_a_stop_already_requested_runs_no_phase_at_all(store):
    calls: list[str] = []

    def step(card: str) -> dict[str, Any]:
        calls.append("ran")
        return {}

    workflow = _workflow(
        THREE_PHASES, {"step.alpha": step, "step.beta": step, "step.gamma": step}
    )

    summary = engine.run_subtask(
        workflow,
        store,
        story_id=STORY_ID,
        subtask=_subtask(),
        repo_dir=REPO,
        should_stop=lambda: True,
    )

    assert calls == []
    assert summary.status == "stopped"
    assert summary.failed_phase is None
    assert summary.detail == "stopped before alpha"
    assert summary.results == {}
    assert _journalled_phases(store) == []
    assert store.connection.execute("SELECT COUNT(*) FROM phases").fetchone()[0] == 0
    assert _subtask_journal_statuses(store) == ["stopped"]
    assert _projected_subtask_status(store) == "stopped"


def test_a_stop_before_a_deterministic_phase_leaves_it_unstarted(store):
    calls: list[str] = []
    flag = _StopFlag()

    def prepare(card: str) -> dict[str, Any]:
        calls.append("prepare")
        return {}

    def finish(card: str) -> dict[str, Any]:
        calls.append("finish")
        return {}

    def agent_runner(phase, context, rendered):
        calls.append(f"agent:{phase.name}")
        flag.set()
        return {"summary": "explored"}

    workflow = _workflow(STOP_MIXED, {"step.prepare": prepare, "step.finish": finish})

    summary = engine.run_subtask(
        workflow,
        store,
        story_id=STORY_ID,
        subtask=_subtask(),
        repo_dir=REPO,
        agent_runner=agent_runner,
        should_stop=flag,
    )

    assert calls == ["prepare", "agent:explore"]
    assert summary.status == "stopped"
    assert summary.detail == "stopped before finish"
    assert summary.results["explore"] == {"summary": "explored"}
    assert _journalled_phases(store) == [("prepare", "started"), ("prepare", "done")]
    assert _projected_subtask_status(store) == "stopped"


def test_a_stop_before_an_agent_phase_leaves_the_runner_uncalled(store):
    recorded: dict[str, Any] = {}
    flag = _StopFlag()

    def prepare(card: str) -> dict[str, Any]:
        flag.set()
        return {}

    def finish(card: str) -> dict[str, Any]:
        raise AssertionError("no phase after the stop may start")

    workflow = _workflow(STOP_MIXED, {"step.prepare": prepare, "step.finish": finish})

    summary = engine.run_subtask(
        workflow,
        store,
        story_id=STORY_ID,
        subtask=_subtask(),
        repo_dir=REPO,
        agent_runner=_recording_runner(recorded),
        should_stop=flag,
    )

    assert recorded == {}
    assert summary.status == "stopped"
    assert summary.failed_phase is None
    assert summary.detail == "stopped before explore"
    assert _projected_phases(store) == [("prepare", "done")]
    assert _subtask_journal_statuses(store) == ["stopped"]


def test_a_stop_before_an_agent_phase_wins_over_a_missing_runner(store):
    """The check sits before the `agent_runner is None` error: a walk that
    stops before its agent phase never reaches that phase, so it has nothing
    to complain about."""
    flag = _StopFlag()

    def prepare(card: str) -> dict[str, Any]:
        flag.set()
        return {}

    def finish(card: str) -> dict[str, Any]:
        raise AssertionError("no phase after the stop may start")

    workflow = _workflow(STOP_MIXED, {"step.prepare": prepare, "step.finish": finish})

    summary = engine.run_subtask(
        workflow,
        store,
        story_id=STORY_ID,
        subtask=_subtask(),
        repo_dir=REPO,
        should_stop=flag,
    )

    assert summary.status == "stopped"
    assert summary.detail == "stopped before explore"
    assert _projected_subtask_status(store) == "stopped"


def test_a_should_stop_that_never_fires_changes_nothing(store):
    calls: list[str] = []
    checks: list[str] = []

    def make(name: str):
        def step(card: str) -> dict[str, Any]:
            calls.append(name)
            return {"phase": name}

        return step

    def never() -> bool:
        checks.append("check")
        return False

    workflow = _workflow(
        THREE_PHASES,
        {"step.alpha": make("alpha"), "step.beta": make("beta"), "step.gamma": make("gamma")},
    )

    summary = engine.run_subtask(
        workflow,
        store,
        story_id=STORY_ID,
        subtask=_subtask(),
        repo_dir=REPO,
        should_stop=never,
    )

    assert calls == ["alpha", "beta", "gamma"]
    assert checks == ["check", "check", "check"]
    assert summary == engine.SubtaskSummary(
        status="done",
        results={
            "alpha": {"phase": "alpha"},
            "beta": {"phase": "beta"},
            "gamma": {"phase": "gamma"},
        },
    )
    assert _journalled_phases(store) == [
        ("alpha", "started"),
        ("alpha", "done"),
        ("beta", "started"),
        ("beta", "done"),
        ("gamma", "started"),
        ("gamma", "done"),
    ]
    assert _projected_phases(store) == [("alpha", "done"), ("beta", "done"), ("gamma", "done")]
    assert _subtask_journal_statuses(store) == ["done"]
    assert _projected_subtask_status(store) == "done"


def test_should_stop_is_checked_only_at_visited_phases(store):
    """`plan_check` jumps to `implement`; `spec` and `plan` are never visited,
    so never checked, and nothing is checked after the last phase."""
    calls: list[str] = []

    def has(result: dict[str, Any]) -> bool:
        return True

    def never() -> bool:
        calls.append("check")
        return False

    workflow = _skipping_workflow(has, calls)

    summary = engine.run_subtask(
        workflow,
        store,
        story_id=STORY_ID,
        subtask=_subtask(),
        repo_dir=REPO,
        should_stop=never,
    )

    assert calls == ["check", "plan_check", "check", "implement"]
    assert summary.status == "done"
    assert summary.skipped == ["spec", "plan"]


def test_an_escalation_during_the_stop_request_wins_over_the_stop(store):
    calls: list[str] = []
    flag = _StopFlag()

    def alpha(card: str) -> dict[str, Any]:
        calls.append("alpha")
        return {}

    def beta(card: str) -> dict[str, Any]:
        calls.append("beta")
        flag.set()
        raise OSError("disk went away")

    def gamma(card: str) -> dict[str, Any]:
        calls.append("gamma")
        return {}

    workflow = _workflow(
        THREE_PHASES, {"step.alpha": alpha, "step.beta": beta, "step.gamma": gamma}
    )

    summary = engine.run_subtask(
        workflow,
        store,
        story_id=STORY_ID,
        subtask=_subtask(),
        repo_dir=REPO,
        should_stop=flag,
    )

    assert calls == ["alpha", "beta"]
    assert summary.status == "escalated"
    assert summary.failed_phase == "beta"
    assert "disk went away" in summary.detail
    assert _subtask_journal_statuses(store) == ["escalated"]
    assert _projected_subtask_status(store) == "escalated"


def test_an_exception_from_should_stop_propagates_and_records_nothing(store):
    calls: list[str] = []

    def step(card: str) -> dict[str, Any]:
        calls.append("ran")
        return {}

    def broken() -> bool:
        raise RuntimeError("stop flag unreadable")

    workflow = _workflow(
        THREE_PHASES, {"step.alpha": step, "step.beta": step, "step.gamma": step}
    )

    with pytest.raises(RuntimeError, match="stop flag unreadable"):
        engine.run_subtask(
            workflow,
            store,
            story_id=STORY_ID,
            subtask=_subtask(),
            repo_dir=REPO,
            should_stop=broken,
        )

    assert calls == []
    # Nothing was ever written, so the journal file does not even exist yet
    # (`journal.read()` would raise MissingJournalError).
    assert not store.journal.path.exists()
    assert _projected_phases(store) == []
    assert _projected_subtask_status(store) is None


def test_a_stopped_subtask_can_be_driven_again_to_done(store):
    calls: list[str] = []
    flag = _StopFlag()

    def make(name: str, *, stop: bool = False):
        def step(card: str) -> dict[str, Any]:
            calls.append(name)
            if stop:
                flag.set()
            return {"phase": name}

        return step

    workflow = _workflow(
        THREE_PHASES,
        {
            "step.alpha": make("alpha"),
            "step.beta": make("beta", stop=True),
            "step.gamma": make("gamma"),
        },
    )

    first = engine.run_subtask(
        workflow,
        store,
        story_id=STORY_ID,
        subtask=_subtask(),
        repo_dir=REPO,
        should_stop=flag,
    )

    assert first.status == "stopped"
    assert first.detail == "stopped before gamma"
    assert calls == ["alpha", "beta"]
    assert _projected_subtask_status(store) == "stopped"

    calls.clear()
    second = engine.run_subtask(
        workflow,
        store,
        story_id=STORY_ID,
        subtask=_subtask(),
        repo_dir=REPO,
        start_phase="gamma",
    )

    assert calls == ["gamma"]
    assert second.status == "done"
    assert second.detail is None
    assert _projected_phases(store) == [("alpha", "done"), ("beta", "done"), ("gamma", "done")]
    assert _subtask_journal_statuses(store) == ["stopped", "done"]
    assert _projected_subtask_status(store) == "done"
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `uv run pytest tests/test_engine.py -k "stop" -v`
Expected: the new tests FAIL with `TypeError: run_subtask() got an unexpected keyword argument 'should_stop'` (the pre-existing `test_a_subtask_summary_may_report_stopped` still PASSES; if it fails, the sibling precondition is missing -- stop and report).

- [ ] **Step 3: Add the `should_stop` keyword to `run_subtask`**

In `src/agent_manager/engine.py`, replace the signature tail:

```python
    agent_runner: AgentPhaseRunner | None = None,
    start_phase: str | None = None,
    clock: Clock = _utcnow,
) -> SubtaskSummary:
```

with:

```python
    agent_runner: AgentPhaseRunner | None = None,
    start_phase: str | None = None,
    clock: Clock = _utcnow,
    should_stop: Callable[[], bool] | None = None,
) -> SubtaskSummary:
```

And append this paragraph to the end of the `run_subtask` docstring, just before its closing `"""`:

```python

    `should_stop` is the cooperative stop (addendum P4): asked once before each
    phase the walk actually visits, never during one and never after the last.
    A true answer records the subtask `stopped` and returns without starting
    that phase. A phase already running finishes and is recorded as normal. An
    exception from it is the caller's and is not caught.
```

- [ ] **Step 4: Put the check at the head of the loop**

In `src/agent_manager/engine.py`, replace:

```python
    while index < len(workflow.phases):
        phase = workflow.phases[index]
        if not isinstance(phase, DeterministicPhase):
```

with:

```python
    while index < len(workflow.phases):
        phase = workflow.phases[index]
        # Before anything about the phase runs -- the missing-runner error,
        # `render_prompt`, the `started` row -- so a stop starts nothing.
        if should_stop is not None and should_stop():
            return _stop(summary, store, story_id, subtask, phase.name)
        if not isinstance(phase, DeterministicPhase):
```

- [ ] **Step 5: Add the `_stop` helper after `_escalate`**

Append to the end of `src/agent_manager/engine.py`:

```python


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

- [ ] **Step 6: Run the engine tier to verify it passes**

Run: `uv run pytest tests/test_engine.py -v`
Expected: PASS for every test, old and new.

- [ ] **Step 7: Commit**

```bash
git add src/agent_manager/engine.py tests/test_engine.py
git commit -m "feat(engine): stop between phases when should_stop asks"
```

---

### Task 2: `cli.drive_subtask` and `orchestrate.Driver` carry `should_stop`

**Files:**
- Modify: `src/agent_manager/cli.py:679-690` (signature), `:708-719` (engine call)
- Modify: `src/agent_manager/orchestrate.py:53-65` (`Driver.__call__`)
- Test: `tests/test_cli.py` (insert after `test_drive_subtask_drives_two_subtasks_under_one_store_and_run`, which ends at line 1484, before `runner = CliRunner()`)

**Interfaces:**
- Consumes: `engine.run_subtask(..., should_stop=...)` from Task 1; test helpers in tests/test_cli.py: `requires_git`, `requires_brd`, fixtures `project` and `cards` (keys `"story"`, `"subtask"`), `fake_runner(seen)`; module imports already present there and used by the neighbouring test: `cli`, `board`, `dag`, `models`, `store_module`, `datetime`, `timezone`.
- Produces: `cli.drive_subtask(*, ..., runner_factory=None, should_stop: Callable[[], bool] | None = None) -> SubtaskDrive`; `orchestrate.Driver.__call__` with the same trailing keyword.

- [ ] **Step 1: Write the failing CLI pass-through test**

Insert into `tests/test_cli.py` right after `test_drive_subtask_drives_two_subtasks_under_one_store_and_run`:

```python
@requires_git
@requires_brd
def test_drive_subtask_hands_should_stop_to_the_engine(project, cards):
    """Addendum P4: the driver passes the stop check straight through. With a
    stop already requested, the first phase of `builtin/task.yaml` never
    starts, so the fake runner is never called and no worktree is made."""
    root = cli.resolve_repo_dir(project)
    parent = board.show(cards["story"], repo_dir=root)
    card = board.show(cards["subtask"], repo_dir=root)

    started_at = datetime(2026, 9, 24, 12, 0, 0, tzinfo=timezone.utc)
    run_id = cli.mint_run_id(card.id, started_at)
    branch = dag.task_branch("m1", card)
    subtask = models.SubtaskRun(
        card_id=card.id,
        branch=branch,
        base_branch="main",
        status="started",
        worktree_path=cli.worktree_for(root, branch),
    )
    seen: list[tuple[str, dict[str, Any]]] = []
    store = store_module.Store.open(root, run_id)
    try:
        store.record_run(
            models.Run(
                id=run_id,
                workflow=cli.WORKFLOW_NAME,
                repo_dir=root,
                base_branch="main",
                branch_prefix="m1",
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
                tip_branch=branch,
            )
        )
        store.record_subtask(parent.id, subtask)

        drive = cli.drive_subtask(
            store=store,
            run_id=run_id,
            card=card,
            parent=parent,
            subtask=subtask,
            repo_dir=root,
            runner_factory=lambda **kwargs: fake_runner(seen),
            should_stop=lambda: True,
        )
        run = store.load_run(run_id)
    finally:
        store.close()

    assert drive.summary.status == "stopped"
    assert drive.summary.failed_phase is None
    assert drive.summary.detail == "stopped before worktree"
    assert seen == []
    assert not subtask.worktree_path.exists()
    assert run is not None
    assert [sub.status for sub in run.stories[0].subtasks] == ["stopped"]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_cli.py::test_drive_subtask_hands_should_stop_to_the_engine -v`
Expected: FAIL with `TypeError: drive_subtask() got an unexpected keyword argument 'should_stop'` (or SKIPPED if `git`/`brd` are not installed -- in that case note it and rely on Step 6's full run on a machine that has them).

- [ ] **Step 3: Add the keyword to `cli.drive_subtask` and pass it through**

In `src/agent_manager/cli.py`, replace:

```python
    allow_no_verification: bool = False,
    runner_factory: RunnerFactory | None = None,
) -> SubtaskDrive:
    """Walk one subtask through `builtin/task.yaml` under a store the caller owns.
```

with:

```python
    allow_no_verification: bool = False,
    runner_factory: RunnerFactory | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> SubtaskDrive:
    """Walk one subtask through `builtin/task.yaml` under a store the caller owns.
```

Then replace:

```python
        extra_context=gate_context(commands, allow_no_verification),
        agent_runner=runner,
    )
```

with:

```python
        extra_context=gate_context(commands, allow_no_verification),
        agent_runner=runner,
        should_stop=should_stop,
    )
```

And in the same docstring, replace the sentence:

```python
    nothing. An escalation is `summary.status == "escalated"`, not an exception.
    """
```

with:

```python
    nothing. An escalation is `summary.status == "escalated"`, not an exception.
    `should_stop` goes straight to `engine.run_subtask`; a stop is
    `summary.status == "stopped"`.
    """
```

- [ ] **Step 4: Add the keyword to `orchestrate.Driver`**

In `src/agent_manager/orchestrate.py`, replace:

```python
        allow_no_verification: bool = False,
        runner_factory: cli.RunnerFactory | None = None,
    ) -> cli.SubtaskDrive: ...
```

with:

```python
        allow_no_verification: bool = False,
        runner_factory: cli.RunnerFactory | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> cli.SubtaskDrive: ...
```

Do not change `run_milestone`'s `drive(...)` call or `tests/test_orchestrate.py::FakeDriver`.

- [ ] **Step 5: Run the CLI and orchestrate tiers to verify they pass**

Run: `uv run pytest tests/test_cli.py tests/test_orchestrate.py -v`
Expected: PASS, including the new `test_drive_subtask_hands_should_stop_to_the_engine` and every existing `FakeDriver` test (which proves the protocol change leaves fake drivers without `should_stop` working).

- [ ] **Step 6: Run the whole default suite**

Run: `uv run pytest`
Expected: PASS, with no failures anywhere, including `tests/e2e` default-collected tests.

- [ ] **Step 7: Commit**

```bash
git add src/agent_manager/cli.py src/agent_manager/orchestrate.py tests/test_cli.py
git commit -m "feat(cli): pass should_stop through drive_subtask and the Driver protocol"
```

---

## Spec coverage map

- Scope 1-3 (keyword, check position, recording `stopped`, `failed_phase` None, detail text): Task 1 Steps 3-5; tests `test_a_stop_requested_during_phase_three_stops_before_phase_four`, `test_a_stop_already_requested_runs_no_phase_at_all`.
- Scope 4 (never during a phase, escalation wins, no check after last): `test_a_stop_requested_during_phase_three_stops_before_phase_four`, `test_an_escalation_during_the_stop_request_wins_over_the_stop`, `test_should_stop_is_checked_only_at_visited_phases`.
- Scope 5 (skipped phases not checked): `test_should_stop_is_checked_only_at_visited_phases`.
- Scope 6 (cli + Driver): Task 2.
- Scope 7 (callers and fakes unchanged): Task 2 Step 4 note; existing `tests/test_orchestrate.py` suite in Step 5.
- Observable behaviour (never fires = today; re-entry): `test_a_should_stop_that_never_fires_changes_nothing`, `test_a_stopped_subtask_can_be_driven_again_to_done`, full suite in Task 2 Step 6.
- Error paths: `test_an_exception_from_should_stop_propagates_and_records_nothing`; `test_a_stop_before_an_agent_phase_wins_over_a_missing_runner` (existing `test_an_agent_phase_with_no_runner_is_a_named_engine_error` keeps the reached-phase case).
- Spec tests 1-7: tests 1-5 in Task 1, test 6 in Task 2, test 7 intentionally not added (reason in File Structure).
