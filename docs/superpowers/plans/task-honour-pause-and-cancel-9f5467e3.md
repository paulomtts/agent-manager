<!-- task-pipeline: validated -->
# Honour pause and cancel in `--card` runs (card 9f5467e3)

Subtask of story 09a1fc18 "Honour pause and cancel in milestone and card runs" (milestone bdc5838b). Narrows live-control design C11 (`docs/superpowers/specs/2026-09-27-live-control-design.md:170-176`, on branch `docs/live-control`) and plan Task 2.3 (`docs/superpowers/plans/2026-09-27-live-control.md:408-421`). No new design here. Builds on the m9 branch chain (base: sibling df0e9d20's branch), where `control.py`, `StopSignal.request`, the store's lease/control tables, `am pause`/`am cancel` and `resume_run`'s C9/C10 refusals already exist. Read the code as built before editing; plan line numbers are from master@6d69e3a.

## Scope

In scope, and only this:

- `src/agent_manager/cli.py`: `run_card` (~793) and `_resume_from_checkpoint` (~1394).
- Their tests in `tests/test_cli.py`.

Out of scope (owned by done siblings, do not touch): `orchestrate.run_milestone`, `controlled_payload`, `resumable_milestone_run` (0e1edf31); `request_control`, the `pause`/`cancel` commands, `status_payload`'s `control`, `resume_run`'s cancelled/live refusals (df0e9d20). `control.py`, `runtime/stop.py`, `store.py` and `models.py` are reused as-is. `drive_subtask` (sync) stays unchanged for any other caller.

## Behaviour

Both functions:

- Gain a keyword `control_interval: float = control.CONTROL_POLL_SECONDS`.
- Keep every existing pre-store refusal and ordering (board reads before a run id exists; `checkpoint_resume_phase` refusal before the first write; orphan attempts marked `harness_error`; run/story/subtask recorded `started`).
- Hold `with control.Lease(store) as lease:` around the walk and the final records (the plan places it right after the `started` records; wrapping from the `started` `record_run` on is equivalent and acceptable). The lease is released on every exit, including when the walk raises.
- Replace the `drive_subtask(...)` call with `stop = StopSignal()` and `asyncio.run(control.controlled(drive_subtask_async(..., stop=stop), store=store, stop=stop, lease=lease, interval=control_interval))`, passing the same arguments as today (`resume_from=checkpoint` in `_resume_from_checkpoint`). This mirrors `orchestrate.run_milestone`'s wiring.
- Final records: run status is `"cancelled"` when `stop.requested == "cancel"`, else `summary.status`. Story and subtask rows always get `summary.status` (a cancelled run's subtask and story stay `stopped` as the park left them). The payload's `"status"` is the run's status. All other payload keys are unchanged.

Resulting outcomes:

- Pause while a phase runs: the running phase finishes (no phase is cancelled), the engine parks before the next phase through `ON_PAUSE` + `Parked`, the newest checkpoint is `parked`, run/story/subtask are `stopped`, payload `status == "stopped"`, exit 0. `am resume <run-id>` continues from the parked phase via `select_resumable` and can end `done`.
- Cancel: same park; run `cancelled`, story/subtask `stopped`, payload `status == "cancelled"`, exit 0. `am resume` refuses it (exit 3, existing `NotResumableError` from df0e9d20).
- No request: behaviour and payload identical to today.
- A control never yields `escalated` or `failed_phase`. If the subtask escalates and a cancel was also applied, the run is `cancelled` (C6 precedence) and exits 0; with only a pause, `summary.status` stands (`escalated` -> exit 1).

Exit codes are unchanged: the existing mapping keys off `payload["status"] == "escalated"` (cli.py ~1231, ~1612), so `stopped` and `cancelled` exit 0 and `escalated` exits 1; confirm it still holds with the new run status. CLI envelope unchanged (`{"ok": true, "data": ...}`, errors `{"ok": false, "error": {"type","message"}}` exit 3, `--pretty`).

## Constraints

- SQLite is the only control channel; no new dependency, socket, fifo or signal handler. No schema, CHECK or journal changes; lease/control rows are never journalled.
- The only stop mechanism is `StopSignal` + `ON_PAUSE` + `Parked`; never cancel a running phase to honour a control, and nothing runs past the park.
- `control.py` must not import `cli`; `cli.py` already imports `control`. Only `orchestrate.py` imports grafo.

## Error paths

- Walk raises (e.g. `EngineError`): `controlled` cancels the walk, closes the window, sweeps, and re-raises; the `Lease` context releases the lease; the store is closed by the existing `finally`; the error surfaces as today. No final status rows are written by this path beyond what exists today.
- Watcher `sqlite3.OperationalError` is retried inside `control.watch`; nothing new to handle here.

## Tests

All in `tests/test_cli.py` (default suite tier: fake runner factories, temporary git repos/boards, no real `claude`), per the repo's placement rule (`pyproject.toml` excludes `-m e2e`; `tests/e2e/` is reserved for the opt-in real-`claude` test). Use the existing `runner_factory` fake-runner pattern and the `requires_git`/`requires_brd` markers. No sleeps for ordering: block fakes on `threading.Event`/`asyncio.Event`; the "second process" is a second `store.open_db` connection that reads the token with `read_lease` and inserts the request (or calls the `am pause`/`am cancel` path). A small `control_interval` may be passed; the fake's first agent phase sends the request, then waits until the stop has fired (e.g. an object with `.pause()` registered on the stop that sets an event) without blocking the loop the watcher runs on.

1. `run --card` paused mid-phase: payload `status == "stopped"`, exit 0; run/story/subtask rows `stopped`; newest checkpoint `parked`; the second phase was not dispatched. Tier: default (`tests/test_cli.py`).
2. `am resume <run-id>` of that paused run continues from the parked phase and ends `done`, exit 0. Tier: default.
3. `run --card` cancelled mid-phase: payload `status == "cancelled"`, exit 0; run row `cancelled`, story/subtask `stopped`; `am resume` then exits 3. Tier: default.
4. Lease released on every exit: after a normal `--card` run `read_lease` is `None`; also after a run whose walk raises `EngineError`. Tier: default.
5. `_resume_from_checkpoint` honours a pause too: a resumed task run paused mid-phase ends `stopped` with a `parked` checkpoint and releases its lease. Tier: default.
6. Regression: an uncontrolled `--card` run's payload and exit code are unchanged (existing tests stay green). Tier: default.

Verification: `uv run pytest` (whole suite, including `tests/e2e` default-unmarked tests) green. No typecheck or lint command.

---

# Honour pause and cancel in `--card` runs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `am run --card` and `am resume` of a `task` run hold a `control.Lease` and run their walk under `control.controlled`, so `am pause` parks the run (`stopped`, resumable) and `am cancel` closes it (`cancelled`, not resumable).

**Architecture:** `run_card` and `_resume_from_checkpoint` in `src/agent_manager/cli.py` stop calling the sync `drive_subtask` and instead do what `orchestrate.run_milestone` does (orchestrate.py:1417-1455): take `control.Lease(store)` inside the `try` that closes the store, build one `StopSignal`, and `asyncio.run(control.controlled(drive_subtask_async(..., stop=stop), store=store, stop=stop, lease=lease, interval=control_interval))`. A new pure helper `card_run_status(summary, stop)` gives the run row's status (C6: `cancelled` wins, else `summary.status`); story and subtask rows keep `summary.status`. Nothing in `control.py`, `runtime/stop.py`, `store.py` or `models.py` changes.

**Tech Stack:** Python 3, Typer, Pydantic, asyncio, SQLite (`store.open_db`), pytest (default tier only), `uv`.

**Spec:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m9/task-honour-pause-and-cancel-9f5467e3/docs/superpowers/specs/task-honour-pause-and-cancel-9f5467e3-design.md` (prepended verbatim above).

**Branch / worktree:** `m9/task-honour-pause-and-cancel-9f5467e3` in `/home/paulomtts/Code/agent-manager/.claude/worktrees/m9/task-honour-pause-and-cancel-9f5467e3`, cut from `m9/task-add-am-pause-and-am-df0e9d20`. Every path below is relative to that worktree. Nothing is pushed; the base branch never moves.

## Global Constraints

- SQLite is the only control channel; no new runtime dependency, no socket, fifo or signal handler (C1).
- No schema, CHECK or journal changes; `run_controls`/`run_leases` rows are never journalled.
- The only stop mechanism is `StopSignal` + `ON_PAUSE` + `Parked`; never cancel a running phase to honour a control; nothing runs past the park.
- `control.py` never imports `cli` or `orchestrate`; only `orchestrate.py` imports grafo. `cli.py` already imports `control` (cli.py:34) and `StopSignal` (cli.py:42); add no new imports to `cli.py`.
- A control never produces `escalated` or a `failed_phase`.
- CLI envelope unchanged: `{"ok": true, "data": ...}` / `{"ok": false, "error": {"type","message"}}` exit 3, `--pretty` supported. Exit mapping unchanged (`escalated` -> 1, everything else ok -> 0).
- Do not touch `orchestrate.run_milestone`, `controlled_payload`, `resumable_milestone_run`, `request_control`, the `pause`/`cancel` commands, `status_payload`, `resume_run`, or `drive_subtask`.
- Tests: default tier only, all in `tests/test_cli.py`; nothing in `tests/e2e/`. No sleeps for ordering; the "second process" is a second `store.open_db` connection (here via `cli.request_control`, the `am pause`/`am cancel` path).
- Verification for every task: `uv run pytest` (whole suite) green.

## Review Focus

1. A cancel applied while the subtask also escalates: the run must end `cancelled` and exit 0, while the story and subtask rows say `escalated` (C6). Pinned in Task 1, `test_a_control_and_an_escalation_follow_c6_at_the_command` and `test_a_cancel_that_meets_an_escalation_closes_the_run_but_keeps_the_rows`.
2. A pause applied while the subtask escalates: the escalation stands (`escalated`, exit 1). A pause must not hide an escalation as `stopped`. Pinned in Task 1, same parametrized command test.
3. A pause or cancel sent after the run has ended: it must be refused (exit 3, `NotRunningError`) and never queued for a process that is gone. Pinned in Task 1, `test_a_cancelled_card_run_closes_the_run_and_resume_refuses_it` (the `am pause` afterwards).
4. The request row itself: once honoured it must be marked handled, so `am status` does not show a pending pause for a finished run. Pinned in Task 1, `test_a_paused_card_run_parks_after_the_running_phase` (`handled_at` asserted).
5. A resumed `task` run that is cancelled: it must also close for good (`cancelled`) and be refused by a second `am resume`. Pinned in Task 2, `test_a_resumed_card_run_cancelled_mid_phase_is_closed_for_good`.

---

### Task 1: `run_card` holds a lease and honours pause and cancel

**Files:**
- Modify: `src/agent_manager/cli.py:793-889` (`run_card`), and add `card_run_status` just above it (after `drive_subtask`, which ends at cli.py:790).
- Test: `tests/test_cli.py` (append a new section at the end of the file).

**Interfaces:**
- Consumes (already on this branch, unchanged): `control.Lease(store)` context manager with `.token`; `control.controlled(work, *, store, stop, lease, interval)` -> `work`'s result; `control.CONTROL_POLL_SECONDS: float`; `StopSignal().requested: Literal["pause","cancel"] | None`; `drive_subtask_async(*, store, run_id, card, parent, subtask, repo_dir, commands=(), allow_no_verification=False, runner_factory=None, stop=None, resume_from=None) -> SubtaskDrive`; `cli.request_control(run_id, command, *, repo_dir, clock=_utcnow) -> dict`; `store_module.read_lease(conn, run_id) -> LeaseRow | None`; `store_module.control_requests(conn, run_id, *, lease=None) -> list[ControlRow]`.
- Produces:
  - `cli.card_run_status(summary: SubtaskSummary, stop: StopSignal) -> str` — `"cancelled"` iff `stop.requested == "cancel"`, else `summary.status`.
  - `cli.run_card(..., control_interval: float = control.CONTROL_POLL_SECONDS) -> dict[str, Any]` — new trailing keyword; payload keys unchanged.
  - Test helpers used again by Task 2 (module level in `tests/test_cli.py`): `CONTROL_TICK: float`, fixture `control_applied -> threading.Event`, `_card_lease(project: Path, run_id: str) -> LeaseRow | None`, `_card_controls(project: Path, run_id: str) -> list[ControlRow]`, `_card_statuses(project: Path, run_id: str) -> dict[str, str]`, `_newest_checkpoint_reason(project: Path, run_id: str) -> str`, `_controlling_factory(project, applied, *, command, at, seen, fail=False, leases=None)`.

- [ ] **Step 1: Write the failing pure test for `card_run_status`**

Append to the end of `tests/test_cli.py`:

```python
# ── live control in --card runs (card 9f5467e3) ─────────────────────────────


@pytest.mark.parametrize(
    "command, summary_status, expected",
    [
        (None, "done", "done"),
        (None, "escalated", "escalated"),
        (None, "stopped", "stopped"),
        ("pause", "stopped", "stopped"),
        ("pause", "escalated", "escalated"),
        ("cancel", "stopped", "cancelled"),
        ("cancel", "escalated", "cancelled"),
        ("cancel", "done", "cancelled"),
    ],
)
def test_card_run_status_follows_c6_precedence(command, summary_status, expected):
    """A cancel closes the run whatever the walk ended as; a pause never
    changes it, so a paused escalation stays `escalated`."""
    stop = StopSignal()
    if command is not None:
        stop.request(command)

    assert cli.card_run_status(SubtaskSummary(status=summary_status), stop) == expected
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_cli.py -k card_run_status_follows_c6_precedence -v`
Expected: FAIL, every case with `AttributeError: module 'agent_manager.cli' has no attribute 'card_run_status'`.

- [ ] **Step 3: Add `card_run_status` to `cli.py`**

In `src/agent_manager/cli.py`, insert between the end of `drive_subtask` (the closing `)` of its `return asyncio.run(...)`, cli.py:790) and `def run_card(` (cli.py:793):

```python
def card_run_status(summary: SubtaskSummary, stop: StopSignal) -> str:
    """The run row's status after a `task` walk (live control C6, C11).

    A cancel closes the run for good whatever the walk ended as, so it wins
    even over an escalation. A pause changes nothing: the walk already parked
    as `stopped`, and an escalation it met stays `escalated`. The story and
    subtask rows always keep `summary.status`.
    """
    if stop.requested == "cancel":
        return "cancelled"
    return summary.status
```

- [ ] **Step 4: Run it to verify it passes**

Run: `uv run pytest tests/test_cli.py -k card_run_status_follows_c6_precedence -v`
Expected: PASS (8 cases).

- [ ] **Step 5: Write the failing `run_card` behaviour tests and their helpers**

Append to the end of `tests/test_cli.py`:

```python
CONTROL_TICK = 0.01
"""How often the run's watcher polls in these tests. The request is inserted
mid-phase and the fake waits on `control_applied`, so this sets only how soon
the watcher notices, never the ordering."""


@pytest.fixture
def control_applied(monkeypatch) -> threading.Event:
    """Set once the running `am` process's watcher has applied a request.

    `run_card` builds its `StopSignal` by the module name `cli.StopSignal`, so
    this subclass is the one it gets. `request` pauses every registered agent
    before the event is set, so a fake that waits on it returns from its phase
    with the agent already paused -- no sleep decides the order.
    """
    applied = threading.Event()

    class SignalledStop(StopSignal):
        def request(self, command):
            changed = super().request(command)
            applied.set()
            return changed

    monkeypatch.setattr(cli, "StopSignal", SignalledStop)
    return applied


def _card_lease(project: Path, run_id: str) -> store_module.LeaseRow | None:
    """The run's lease row, read over a second connection as `am status` would."""
    conn = store_module.open_db(cli.resolve_repo_dir(project))
    try:
        return store_module.read_lease(conn, run_id)
    finally:
        conn.close()


def _card_controls(project: Path, run_id: str) -> list[store_module.ControlRow]:
    conn = store_module.open_db(cli.resolve_repo_dir(project))
    try:
        return store_module.control_requests(conn, run_id)
    finally:
        conn.close()


def _card_statuses(project: Path, run_id: str) -> dict[str, str]:
    """The run, its one story and its one subtask, as recorded."""
    run = _loaded(project, run_id)
    (story,) = run.stories
    (subtask,) = story.subtasks
    return {"run": run.status, "story": story.status, "subtask": subtask.status}


def _newest_checkpoint_reason(project: Path, run_id: str) -> str:
    rows = [row for row in _checkpoint_rows(project) if row[0] == run_id]
    assert rows, f"run {run_id} saved no checkpoint"
    return rows[-1][3]


def _controlling_factory(
    project: Path,
    applied: threading.Event,
    *,
    command: str | None,
    at: str,
    seen: list[str],
    fail: bool = False,
    leases: list[store_module.LeaseRow | None] | None = None,
):
    """A `cli.RunnerFactory` whose runner, inside phase `at`, acts as a second process.

    The runner runs in the engine's `to_thread` worker, so blocking it never
    blocks the loop the watcher runs on. In phase `at` it records the lease
    as another process sees it (`leases`), sends `command` through
    `cli.request_control` (the `am pause`/`am cancel` path, over its own
    connection), waits until the watcher applied it, and then finishes the
    phase -- or, with `fail`, escalates it with a gate failure. Every other
    phase is `recording_runner`'s.
    """
    record = _resume_factory(seen)

    def factory(*, store, run_id, story_id, card_id):
        run = record(store=store, run_id=run_id, story_id=story_id, card_id=card_id)

        def runner(phase, context, rendered):
            if phase.name != at:
                return run(phase, context, rendered)
            if leases is not None:
                leases.append(_card_lease(project, run_id))
            if command is not None:
                cli.request_control(run_id, command, repo_dir=project)
                if not applied.wait(5):
                    raise RuntimeError(f"the {command} request was never applied")
            if fail:
                seen.append(phase.name)
                raise AgentPhaseFailed(
                    phase.name, outcome="gate_failed", detail="canned gate failure"
                )
            return run(phase, context, rendered)

        return runner

    return factory


def _controlled_card_run(project: Path, cards: dict[str, str], factory) -> dict[str, Any]:
    return cli.run_card(
        cards["subtask"],
        repo_dir=project,
        base_branch="main",
        branch_prefix="m1",
        runner_factory=factory,
        control_interval=CONTROL_TICK,
    )


@requires_git
@requires_brd
def test_a_paused_card_run_parks_after_the_running_phase(project, cards, control_applied):
    """Spec test 1: the running phase finishes, nothing after it is
    dispatched, the park is `parked`, every row is `stopped`, and the request
    is marked handled."""
    seen: list[str] = []
    factory = _controlling_factory(
        project, control_applied, command="pause", at="spec", seen=seen
    )

    payload = _controlled_card_run(project, cards, factory)

    run_id = payload["run_id"]
    assert payload["status"] == "stopped", payload
    assert payload["failed_phase"] is None
    assert payload["detail"] == "stopped before validate_spec"
    assert seen[-1] == "spec"
    assert "validate_spec" not in seen
    assert _card_statuses(project, run_id) == {
        "run": "stopped",
        "story": "stopped",
        "subtask": "stopped",
    }
    assert _newest_checkpoint_reason(project, run_id) == "parked"
    controls = _card_controls(project, run_id)
    assert [row.command for row in controls] == ["pause"]
    assert all(row.handled_at is not None for row in controls)
    assert _card_lease(project, run_id) is None


@requires_git
@requires_brd
def test_a_paused_card_run_resumes_from_the_parked_phase_to_done(
    project, cards, control_applied
):
    """Spec test 2: `am resume` continues at the phase the pause parked
    before, and ends `done`."""
    factory = _controlling_factory(
        project, control_applied, command="pause", at="spec", seen=[]
    )
    run_id = _controlled_card_run(project, cards, factory)["run_id"]

    after: list[str] = []
    payload = cli.resume_run(run_id, repo_dir=project, runner_factory=_resume_factory(after))

    assert payload["status"] == "done", payload
    assert payload["resumed_from"] == "validate_spec"
    assert after[0] == "validate_spec"
    assert not {"explore", "spec"} & set(after)
    assert _card_statuses(project, run_id) == {
        "run": "done",
        "story": "done",
        "subtask": "done",
    }
    assert _card_lease(project, run_id) is None


@requires_git
@requires_brd
def test_a_cancelled_card_run_closes_the_run_and_resume_refuses_it(
    project, cards, control_applied
):
    """Spec test 3, plus Review Focus 3: the run is `cancelled`, its story and
    subtask stay `stopped` as the park left them, `am resume` refuses it, and
    a pause sent afterwards is refused rather than queued."""
    seen: list[str] = []
    factory = _controlling_factory(
        project, control_applied, command="cancel", at="spec", seen=seen
    )

    payload = _controlled_card_run(project, cards, factory)

    run_id = payload["run_id"]
    assert payload["status"] == "cancelled", payload
    assert payload["failed_phase"] is None
    assert "validate_spec" not in seen
    assert _card_statuses(project, run_id) == {
        "run": "cancelled",
        "story": "stopped",
        "subtask": "stopped",
    }
    assert _newest_checkpoint_reason(project, run_id) == "parked"
    assert _card_lease(project, run_id) is None

    resumed = runner.invoke(cli.app, ["resume", run_id, "--repo-dir", str(project)])
    assert resumed.exit_code == cli.EXIT_ERROR, resumed.output
    error = json.loads(resumed.stdout)["error"]
    assert error["type"] == "NotResumableError"
    assert "cancelled" in error["message"]

    late = runner.invoke(cli.app, ["pause", run_id, "--repo-dir", str(project)])
    assert late.exit_code == cli.EXIT_ERROR, late.output
    assert json.loads(late.stdout)["error"]["type"] == "NotRunningError"
    assert [row.command for row in _card_controls(project, run_id)] == ["cancel"]


@requires_git
@requires_brd
def test_a_cancel_that_meets_an_escalation_closes_the_run_but_keeps_the_rows(
    project, cards, control_applied
):
    """Review Focus 1 at the function: C6 puts the cancel first for the run,
    while the story and subtask rows keep the escalation the walk ended in."""
    factory = _controlling_factory(
        project, control_applied, command="cancel", at="spec", seen=[], fail=True
    )

    payload = _controlled_card_run(project, cards, factory)

    assert payload["status"] == "cancelled", payload
    assert _card_statuses(project, payload["run_id"]) == {
        "run": "cancelled",
        "story": "escalated",
        "subtask": "escalated",
    }


@pytest.mark.parametrize(
    "command, fail, exit_code, status",
    [
        ("pause", False, 0, "stopped"),
        ("cancel", False, 0, "cancelled"),
        ("cancel", True, 0, "cancelled"),
        ("pause", True, cli.EXIT_ESCALATED, "escalated"),
    ],
    ids=["pause", "cancel", "cancel-over-escalation", "pause-keeps-escalation"],
)
@requires_git
@requires_brd
def test_a_control_and_an_escalation_follow_c6_at_the_command(
    project, cards, control_applied, monkeypatch, command, fail, exit_code, status
):
    """Spec's exit codes and Review Focus 1-2: the command's mapping still keys
    off `escalated`, so `stopped` and `cancelled` exit 0 with an ok envelope
    and a paused escalation still exits 1. `run` passes no interval, so the
    real `run_card` is wrapped to add a short one and the fake factory."""
    real_run_card = cli.run_card
    factory = _controlling_factory(
        project, control_applied, command=command, at="spec", seen=[], fail=fail
    )

    def run_card_with_control(card_id, **kwargs):
        return real_run_card(
            card_id, **kwargs, runner_factory=factory, control_interval=CONTROL_TICK
        )

    monkeypatch.setattr(cli, "run_card", run_card_with_control)

    result = _invoke(project, cards["subtask"])

    assert result.exit_code == exit_code, result.output
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is True
    assert envelope["data"]["status"] == status


@requires_git
@requires_brd
def test_an_uncontrolled_card_run_holds_its_lease_then_releases_it(project, cards):
    """Spec tests 4 and 6: the lease is held, window open, while a phase runs;
    it is gone afterwards; the payload is today's, key for key."""
    leases: list[store_module.LeaseRow | None] = []
    factory = _controlling_factory(
        project, threading.Event(), command=None, at="explore", seen=[], leases=leases
    )

    payload = _controlled_card_run(project, cards, factory)

    run_id = payload["run_id"]
    (during,) = leases
    assert during is not None, "no lease was held while the walk ran"
    assert during.run_id == run_id
    assert during.accepting is True
    assert _card_lease(project, run_id) is None
    assert payload["status"] == "done"
    assert set(payload) == {
        "run_id",
        "card_id",
        "story_id",
        "branch",
        "base_branch",
        "worktree",
        "status",
        "failed_phase",
        "detail",
        "skipped",
        "warnings",
    }
    assert _card_statuses(project, run_id) == {
        "run": "done",
        "story": "done",
        "subtask": "done",
    }
    assert _card_controls(project, run_id) == []


@requires_git
@requires_brd
def test_a_card_walk_that_raises_releases_its_lease(project, cards, monkeypatch):
    """Spec test 4 and the error path: the lease was held when the walk blew
    up, it is released on the way out, the error surfaces as is, and no final
    status row is written."""
    run_id = cli.mint_run_id(cards["subtask"], CRASHED_AT)
    held: list[store_module.LeaseRow | None] = []

    async def exploding(*args, **kwargs):
        held.append(_card_lease(project, run_id))
        raise EngineError("no value for a required parameter", phase="explore")

    monkeypatch.setattr(cli.runtime_engine, "run_subtask_async", exploding)

    with pytest.raises(EngineError, match="explore"):
        cli.run_card(
            cards["subtask"],
            repo_dir=project,
            base_branch="main",
            branch_prefix="m1",
            clock=lambda: CRASHED_AT,
            runner_factory=lambda **kwargs: fake_runner(),
            control_interval=CONTROL_TICK,
        )

    (during,) = held
    assert during is not None, "no lease was held while the walk ran"
    assert _card_lease(project, run_id) is None
    assert _loaded(project, run_id).status == "started"
```

- [ ] **Step 6: Run them to verify they fail**

Run: `uv run pytest tests/test_cli.py -k "paused_card_run or cancelled_card_run or cancel_that_meets or c6_at_the_command or uncontrolled_card_run or card_walk_that_raises" -v`
Expected: FAIL. The function-level tests fail with `TypeError: run_card() got an unexpected keyword argument 'control_interval'`. In the command test, the `pause`, `cancel` and `cancel-over-escalation` cases fail on the exit code (the wrapper's `TypeError` is not in `HANDLED`, so `CliRunner` reports exit 1); `pause-keeps-escalation` may already pass, because before the change `request_control` refuses with `DeadRunError` inside the phase, which escalates too. It is a guard, and it must still pass after Step 7.

- [ ] **Step 7: Rewrite `run_card` to hold the lease and run under `controlled`**

In `src/agent_manager/cli.py`, replace `run_card` (cli.py:793-889, from `def run_card(` through its closing `store.close()`) with:

```python
def run_card(
    card_id: str,
    *,
    repo_dir: Path,
    branch_prefix: str,
    base_branch: str = "master",
    allow_no_verification: bool = False,
    commands: Sequence[str] = (),
    runner_factory: RunnerFactory | None = None,
    clock: Callable[[], datetime] = _utcnow,
    control_interval: float = control.CONTROL_POLL_SECONDS,
) -> dict[str, Any]:
    """Drive one subtask card through `workflow.task.TASK` once, and report.

    The order is the spec's and it is load-bearing: the board reads happen before
    a run id exists (so a bad card leaves no run directory), and the run, story
    and subtask rows are written before the walk starts (so `status` and `resume`
    can see a run that died on its first phase).

    Live control (C11): from the `started` rows through the final ones the run
    holds a `control.Lease`, and the walk runs under `control.controlled`,
    which polls for `am pause`/`am cancel` every `control_interval` seconds
    and turns one into `stop.request`. A pause parks the walk before its next
    phase (`stopped`, resumable); a cancel parks it the same way and records
    the run `cancelled` (`card_run_status`). No control cancels a running phase.
    """
    root = resolve_repo_dir(repo_dir)
    card = board.show(card_id, repo_dir=root)
    if not card.parent_id:
        raise ParentlessCardError(
            f"card {card.id} ({card.title!r}) has no parent card; `run --card` drives "
            "a subtask of a story, and the story is what every run record is keyed by"
        )
    parent = board.show(card.parent_id, repo_dir=root)

    branch = dag.task_branch(branch_prefix, card)
    worktree = worktree_for(root, branch)
    started_at = clock()
    run_id = mint_run_id(card.id, started_at)

    store = Store.open(root, run_id)
    try:
        run_record = models.Run(
            id=run_id,
            workflow=WORKFLOW_NAME,
            repo_dir=root,
            base_branch=base_branch,
            branch_prefix=branch_prefix,
            status="started",
            started_at=started_at,
            config=models.RunConfig(),
        )
        story = models.StoryRun(
            card_id=parent.id,
            title=parent.title,
            level=0,
            status="started",
            tip_branch=branch,
        )
        subtask = models.SubtaskRun(
            card_id=card.id,
            branch=branch,
            base_branch=base_branch,
            status="started",
            worktree_path=worktree,
        )
        # Inside the `try` that closes the store, so the lease is released
        # before `store.close()` on every exit, a raising walk included (C2).
        with control.Lease(store) as lease:
            store.record_run(run_record)
            store.record_story(story)
            store.record_subtask(story.card_id, subtask)

            stop = StopSignal()
            # `controlled` only ever parks the walk through `stop` (C3); it
            # closes the window and runs a final sweep before returning.
            drive = asyncio.run(
                control.controlled(
                    drive_subtask_async(
                        store=store,
                        run_id=run_id,
                        card=card,
                        parent=parent,
                        subtask=subtask,
                        repo_dir=root,
                        commands=commands,
                        allow_no_verification=allow_no_verification,
                        runner_factory=runner_factory,
                        stop=stop,
                    ),
                    store=store,
                    stop=stop,
                    lease=lease,
                    interval=control_interval,
                )
            )
            summary = drive.summary
            run_status = card_run_status(summary, stop)

            store.record_run(run_record.model_copy(update={"status": run_status}))
            store.record_story(story.model_copy(update={"status": summary.status}))
            store.record_subtask(
                story.card_id, subtask.model_copy(update={"status": summary.status})
            )

        return {
            "run_id": run_id,
            "card_id": card.id,
            "story_id": parent.id,
            "branch": branch,
            "base_branch": base_branch,
            "worktree": str(worktree),
            "status": run_status,
            "failed_phase": summary.failed_phase,
            "detail": summary.detail,
            "skipped": list(summary.skipped),
            "warnings": drive.warnings,
        }
    finally:
        store.close()
```

Do not change `drive_subtask`, `run`, or the exit mapping at cli.py:1230-1235: `stopped` and `cancelled` are not `"escalated"`, so both exit 0 already.

- [ ] **Step 8: Run the new tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k "card_run_status or paused_card_run or cancelled_card_run or cancel_that_meets or c6_at_the_command or uncontrolled_card_run or card_walk_that_raises" -v`
Expected: PASS, all of them.

- [ ] **Step 9: Run the whole suite**

Run: `uv run pytest`
Expected: PASS, whole suite. Pay attention to the existing `run_card` tests (`test_run_card_*`, `test_the_command_prints_an_ok_envelope_and_exits_zero`, `test_an_engine_error_escaping_the_walk_reaches_the_operator`) and the crash/resume tests built on `_crash_pygents` (a `_Killed` `BaseException` now passes through `controlled` and the `Lease` exit before it reaches the test). They must be green unchanged. If one fails, fix `run_card`, not the test.

- [ ] **Step 10: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "feat(cli): honour pause and cancel in run --card runs"
```

---

### Task 2: `_resume_from_checkpoint` holds a lease and honours pause and cancel

**Files:**
- Modify: `src/agent_manager/cli.py` `_resume_from_checkpoint` (cli.py:1394-1474 before Task 1; about 30 lines lower after it — find it by name).
- Test: `tests/test_cli.py` (append after Task 1's section).

**Interfaces:**
- Consumes: `cli.card_run_status(summary, stop) -> str` (Task 1); the test helpers from Task 1: `CONTROL_TICK`, fixture `control_applied`, `_card_lease`, `_card_controls`, `_card_statuses`, `_newest_checkpoint_reason`, `_controlling_factory`; the existing `_crash_pygents(project, cards, phase) -> str`, `_loaded(project, run_id) -> models.Run`, `_resume_factory`, `RESUME_KEYS`, `CRASHED_AT`.
- Produces: `cli._resume_from_checkpoint(run, *, root, allow_no_verification, commands, runner_factory, control_interval: float = control.CONTROL_POLL_SECONDS) -> dict[str, Any]`. The payload keys stay `RESUME_KEYS`. `resume_run` keeps calling it without `control_interval` (the production default). `resume_run` is not changed.

- [ ] **Step 1: Write the failing tests**

Append to the end of `tests/test_cli.py`:

```python
def _resume_card_run(project: Path, run_id: str, factory) -> dict[str, Any]:
    """`_resume_from_checkpoint` called as `resume_run` calls it, plus a short
    interval. `resume_run` passes none and is not this card's to change."""
    return cli._resume_from_checkpoint(
        _loaded(project, run_id),
        root=cli.resolve_repo_dir(project),
        allow_no_verification=False,
        commands=(),
        runner_factory=factory,
        control_interval=CONTROL_TICK,
    )


@requires_git
@requires_brd
def test_a_resumed_card_run_paused_mid_phase_parks_and_releases_its_lease(
    project, cards, control_applied
):
    """Spec test 5: the resumed walk starts at `plan`, is paused there, finishes
    `plan`, parks before the next phase, and gives its lease back."""
    run_id = _crash_pygents(project, cards, "plan")
    seen: list[str] = []
    leases: list[store_module.LeaseRow | None] = []
    factory = _controlling_factory(
        project, control_applied, command="pause", at="plan", seen=seen, leases=leases
    )

    payload = _resume_card_run(project, run_id, factory)

    assert payload["status"] == "stopped", payload
    assert payload["failed_phase"] is None
    assert payload["resumed_from"] == "plan"
    assert payload["discarded_attempts"] == [{"phase": "plan", "n": 1}]
    assert set(payload) == RESUME_KEYS
    assert seen == ["plan"]
    assert payload["detail"] == "stopped before validate_plan"
    (during,) = leases
    assert during is not None and during.run_id == run_id and during.accepting is True
    assert _card_lease(project, run_id) is None
    assert _card_statuses(project, run_id) == {
        "run": "stopped",
        "story": "stopped",
        "subtask": "stopped",
    }
    assert _newest_checkpoint_reason(project, run_id) == "parked"
    assert all(row.handled_at is not None for row in _card_controls(project, run_id))


@requires_git
@requires_brd
def test_a_resumed_card_run_cancelled_mid_phase_is_closed_for_good(
    project, cards, control_applied
):
    """Review Focus 5: a cancel reaches a resumed `task` run too, and a second
    `am resume` refuses it."""
    run_id = _crash_pygents(project, cards, "plan")
    factory = _controlling_factory(
        project, control_applied, command="cancel", at="plan", seen=[]
    )

    payload = _resume_card_run(project, run_id, factory)

    assert payload["status"] == "cancelled", payload
    assert _card_statuses(project, run_id) == {
        "run": "cancelled",
        "story": "stopped",
        "subtask": "stopped",
    }
    assert _card_lease(project, run_id) is None

    again = runner.invoke(cli.app, ["resume", run_id, "--repo-dir", str(project)])
    assert again.exit_code == cli.EXIT_ERROR, again.output
    assert json.loads(again.stdout)["error"]["type"] == "NotResumableError"


@requires_git
@requires_brd
def test_a_resumed_card_walk_that_raises_releases_its_lease(project, cards, monkeypatch):
    """Error path on resume: the lease was held when the walk blew up and is
    released on the way out; the run stays `started` as it does today."""
    run_id = _crash_pygents(project, cards, "plan")
    held: list[store_module.LeaseRow | None] = []

    async def exploding(*args, **kwargs):
        held.append(_card_lease(project, run_id))
        raise EngineError("no value for a required parameter", phase="plan")

    monkeypatch.setattr(cli.runtime_engine, "run_subtask_async", exploding)

    with pytest.raises(EngineError, match="plan"):
        _resume_card_run(project, run_id, _resume_factory())

    (during,) = held
    assert during is not None, "no lease was held while the resumed walk ran"
    assert _card_lease(project, run_id) is None
    assert _loaded(project, run_id).status == "started"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_cli.py -k "resumed_card_run or resumed_card_walk" -v`
Expected: FAIL, all three with `TypeError: _resume_from_checkpoint() got an unexpected keyword argument 'control_interval'`.

- [ ] **Step 3: Rewrite `_resume_from_checkpoint`**

In `src/agent_manager/cli.py`, replace the whole `_resume_from_checkpoint` function (from `def _resume_from_checkpoint(` through its closing `store.close()`) with:

```python
def _resume_from_checkpoint(
    run: models.Run,
    *,
    root: Path,
    allow_no_verification: bool,
    commands: Sequence[str],
    runner_factory: RunnerFactory | None,
    control_interval: float = control.CONTROL_POLL_SECONDS,
) -> dict[str, Any]:
    """Continue a `task` run's one in-flight subtask from its newest checkpoint.

    Every refusal that needs no store -- nothing in flight, a card the board
    lost -- comes before `Store.open`. The checkpoint can only be read through
    the store, so its refusals (`checkpoint_resume_phase`) come right after it
    is opened and before the first write. Then the orphan attempts are marked
    `harness_error`, the run, story and subtask are recorded `started`, and
    `drive_subtask_async` walks `TASK` from the checkpoint, whose queue says
    where the walk goes on. A `milestone` run never comes here: `resume_run`
    hands it to `orchestrate.run_milestone` (card 54e4ec29).

    Live control (C11), as in `run_card`: from the `started` rows through the
    final ones this life of the run holds a fresh `control.Lease`, and the walk
    runs under `control.controlled`. A pause parks it `stopped`; a cancel
    parks it and records the run `cancelled` (`card_run_status`).
    """
    story, subtask = select_resumable(run)
    card = board.show(subtask.card_id, repo_dir=root)
    parent = board.show(story.card_id, repo_dir=root)
    orphans = orphan_attempts(subtask)
    resumed = subtask.model_copy(update={"status": "started"})

    store = Store.open(root, run.id)
    try:
        checkpoint = store.latest_checkpoint(subtask.card_id)
        phase = checkpoint_resume_phase(checkpoint, card_id=subtask.card_id, run_id=run.id)
        for orphan, attempt in orphans:
            store.record_attempt(
                story.card_id,
                subtask.card_id,
                orphan.name,
                attempt.model_copy(update={"status": "harness_error"}),
            )
        # After every refusal, and inside the `try` that closes the store, so
        # the lease is released before `store.close()` on every exit (C2).
        with control.Lease(store) as lease:
            store.record_run(run.model_copy(update={"status": "started"}))
            store.record_story(story.model_copy(update={"status": "started"}))
            store.record_subtask(story.card_id, resumed)

            stop = StopSignal()
            drive = asyncio.run(
                control.controlled(
                    drive_subtask_async(
                        store=store,
                        run_id=run.id,
                        card=card,
                        parent=parent,
                        subtask=resumed,
                        repo_dir=root,
                        commands=commands,
                        allow_no_verification=allow_no_verification,
                        runner_factory=runner_factory,
                        stop=stop,
                        resume_from=checkpoint,
                    ),
                    store=store,
                    stop=stop,
                    lease=lease,
                    interval=control_interval,
                )
            )
            summary = drive.summary
            run_status = card_run_status(summary, stop)

            store.record_run(run.model_copy(update={"status": run_status}))
            store.record_story(story.model_copy(update={"status": summary.status}))
            store.record_subtask(
                story.card_id, resumed.model_copy(update={"status": summary.status})
            )

        return {
            "run_id": run.id,
            "card_id": subtask.card_id,
            "story_id": story.card_id,
            "branch": subtask.branch,
            "base_branch": subtask.base_branch,
            "worktree": None
            if subtask.worktree_path is None
            else str(subtask.worktree_path),
            "status": run_status,
            "failed_phase": summary.failed_phase,
            "detail": summary.detail,
            "skipped": list(summary.skipped),
            "warnings": drive.warnings,
            "resumed_from": phase,
            "discarded_attempts": [
                {"phase": orphan.name, "n": attempt.n} for orphan, attempt in orphans
            ],
        }
    finally:
        store.close()
```

Leave `resume_run` and `resume` as they are: `resume_run` calls this function without `control_interval`, so production polls every `control.CONTROL_POLL_SECONDS`. The resume command's exit mapping (`payload.get("status") == "escalated"`) already sends `stopped` and `cancelled` to exit 0.

- [ ] **Step 4: Run the new tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k "resumed_card_run or resumed_card_walk" -v`
Expected: PASS, all three.

- [ ] **Step 5: Run the whole suite**

Run: `uv run pytest`
Expected: PASS, whole suite. The existing pygents resume tests (`test_a_pygents_run_killed_in_plan_resumes_at_plan_from_its_checkpoint`, `test_a_pygents_resume_*`, `test_a_parked_milestone_subtask_resumes_on_pygents_instead_of_being_refused`) and Task 1's tests must be green unchanged. The refusal tests that assert `_resume_state(project) == before` must still pass: the lease is taken only after `checkpoint_resume_phase`, and lease rows are neither journalled nor part of that snapshot.

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "feat(cli): honour pause and cancel when resuming a task run"
```
