<!-- task-pipeline: validated -->
# Subtask df0e9d20: `am pause`, `am cancel`, control in `am status`, guarded `am resume`

Parent story 09a1fc18 (Honour pause and cancel in milestone and card runs), milestone 9 (bdc5838b). This narrows `docs/superpowers/specs/2026-09-27-live-control-design.md` decisions C3, C6, C8-C12 (which is on branch `docs/live-control`, not master) to the CLI layer. It does not add design.

## Scope

- Change only `src/agent_manager/cli.py`. Test only `tests/test_cli.py`.
- Build on the store/control layer that is already in this worktree: `store.run_status`, `store.read_lease` -> `LeaseRow|None`, `store.control_requests(conn, run_id, *, lease=None)` (seq order), `store.immediate(conn)`, `store.add_control(conn, run_id, *, lease, command, requested_at)` (does not commit, so it must run inside `immediate`), and `control.lease_is_live(lease, *, now, host=..., alive=..., stale_after=...)`.
- Do not touch: `orchestrate.py` (sibling 0e1edf31), `run_card`/`_resume_from_checkpoint` Lease/controlled wiring for `--card` runs (sibling 9f5467e3, C11), `control.py`, `store.py`, and the schema. Also out of scope: `am pause --wait`, sockets/fifos/signal handlers, pausing Integrate or one story, and resetting board cards on cancel.
- Add no new runtime dependency. SQLite is the only channel (C1). Nothing journals or rebuilds `run_controls` or `run_leases`.

## Behavior

### New errors

`NotRunningError`, `DeadRunError`, `NotAcceptingError` and `RunIsLiveError` all subclass `CliError`, so they go through the existing `HANDLED` tuple and are reported as `{ok:false, error:{type,message}}` with exit 3.

### `request_control(run_id, command, *, repo_dir, clock=_utcnow) -> dict`

1. Resolve `repo_dir` and open the db with `store_module.open_db`. Close the connection on every path.
2. Run all of the steps below inside one `store_module.immediate(conn)`. Refusals leave no rows.
3. Check in this order (C8), raising the first error that applies:
   1. The run is not in the projection: `UnknownRunError`.
   2. `run_status != "started"`: `NotRunningError`. The message names the actual status and the next step (for example `am resume` or `am status`).
   3. There is no lease, or `control.lease_is_live(lease, now=clock())` is false: `DeadRunError`. The message names the pid, the host and the heartbeat age in seconds (or says there is no lease).
   4. `lease.accepting` is false: `NotAcceptingError`.
4. Idempotence is checked against the rows of this life, meaning `control_requests(conn, run_id, lease=lease.token)`:
   - A `pause` when a `pause` or `cancel` is already recorded is a no-op.
   - A `cancel` when a `cancel` is already recorded is a no-op.
   - A `cancel` after a `pause` is inserted, which upgrades the request.
   - Otherwise, `add_control(..., lease=lease.token, command=command, requested_at=now)`.
5. Return `{run_id, command, effective, requested_at, already_requested, message}`:
   - `effective` is the strongest command recorded for this life (`cancel` > `pause`).
   - `requested_at` is the new row's time, or the existing row's time when the call was a no-op.
   - `already_requested` is a bool.
   - `message` is a one-line human summary.

Rows under older leases do not count toward idempotence, so a resumed run starts clean.

### `am pause RUN_ID` / `am cancel RUN_ID`

- Both take `--repo-dir` (default `.`) and `--pretty`.
- Both follow `resume`'s pattern (cli.py:1477-1519): print `render(ok_envelope(payload))` and exit 0, or on a `HANDLED` error print `render(error_envelope(error))` and exit `EXIT_ERROR` (3).
- A no-op is still exit 0 with `already_requested: true`.

### `am status`

- `status_payload(run, control=None)` gains a `control` key. The key is always present, and `None` input renders as `{"lease": None, "requests": []}`.
- `status_for` (cli.py:1174) reads the key on its own connection, after the run loads and before the connection closes:
  - `lease` is `read_lease`, rendered as `{pid, host, acquired_at, heartbeat_at, accepting, live}` or `null`. Timestamps are ISO strings. `live` is `control.lease_is_live(lease, now=_utcnow())`, computed at read time.
  - `requests` is `control_requests(conn, run_id)` across every life in seq order, rendered as `[{command, requested_at, handled_at|null}]`.
- The shape follows C12 exactly. `status_for` stays read-only.

### `am resume` guard (C9, C10)

In `resume_run` (cli.py:1407), right after the run loads and while the same connection is still open, and before any `Store.open`, checkpoint read or workflow dispatch:

- If `run.status == "cancelled"`, raise `NotResumableError("run was cancelled; start new work with `am run --milestone`")` (the message may be prefixed with the run id). This applies to both the task and milestone workflows.
- If `read_lease` returns a lease and `lease_is_live` is true, raise `RunIsLiveError("run <id> is still running in pid <pid> on <host> (heartbeat <n>s ago); wait for it to exit, or `am status <id>`")`.

Both refusals are read-only: no run directory, row or journal entry is written.

## Test list

All of these tests belong in `tests/test_cli.py`. Section 7 of the live-control spec puts CLI-level refusals, idempotence, status and the resume guard there. They use `CliRunner` against a temporary project, with the run, lease and control rows planted directly through `store.open_db` connections. A "second process" is just a second connection. There are no sleeps, and liveness is made deterministic by planting `heartbeat_at` relative to the injected clock, plus `host`/pid choices (for example the current pid on this host for live, and a stale heartbeat for dead). Engine, milestone and e2e tiers are owned by siblings and the story.

1. Pause on an unknown run id: exit 3, `UnknownRunError`.
2. Pause on a run whose status is not `started` (for example `stopped`): exit 3, `NotRunningError`, and the message names the status.
3. Pause on a started run with no lease, and again with a stale heartbeat: exit 3, `DeadRunError`, and the message names the pid, host and age. No `run_controls` row is written.
4. Pause on a live lease with `accepting=0`: exit 3, `NotAcceptingError`, no row.
5. Pause on a live accepting lease: exit 0. One row is addressed to `lease.token`, with `effective: "pause"` and `already_requested: false`.
6. A second pause is a no-op (`already_requested: true`, still one row). A pause after a cancel is also a no-op.
7. A cancel after a pause inserts a second row, with `effective: "cancel"`. A repeated cancel is a no-op.
8. Rows under an old lease token do not make a pause under the new token a no-op.
9. `--pretty` output parses and has the same envelope for `pause` and `cancel`.
10. `am status` with no lease gives `control == {"lease": null, "requests": []}`. With a planted lease and rows from two lives, it gives the exact C12 keys, `live` true or false as planted, and requests in seq order including `handled_at`.
11. `am resume` on a `cancelled` run (both the task and milestone workflows): exit 3, `NotResumableError` with the C9 message. `Store.open`, `_resume_from_checkpoint` and `orchestrate.run_milestone` are not reached (patched to fail), and no new run directory appears.
12. `am resume` on a run with a live lease: exit 3, `RunIsLiveError` with the pid, host and heartbeat age. It is read-only in the same way as test 11. A dead lease does not block resume.

## Note

The exploration findings handed to this stage were truncated at 8000 characters, cutting off mid-reference in the store.py line list. This spec relies only on signatures that were checked directly in this worktree's `store.py`, `control.py` and `cli.py`.

---

# `am pause` / `am cancel` / status control / resume guard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give operators `am pause RUN_ID` and `am cancel RUN_ID` (recorded as `run_controls` rows addressed to the live lease), show the lease and requests in `am status`, and make `am resume` refuse a cancelled run or a run whose lease is still live.

**Architecture:** Everything lives in `src/agent_manager/cli.py`. `request_control` runs the C8 refusal chain, the idempotence check and the insert inside one `store_module.immediate(conn)` transaction on a fresh `store_module.open_db` connection (the "second process"). `status_for` renders the lease and every request through a small pure-ish `control_view` helper, and `resume_run` gets two read-only guards on the connection it already opens. All new refusals are `CliError` subclasses so the existing `HANDLED` tuple turns them into exit-3 envelopes.

**Tech Stack:** Python 3, Typer, SQLite (`sqlite3`), pytest with `typer.testing.CliRunner`, `uv`.

**Spec:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m9/task-add-am-pause-and-am-df0e9d20/docs/superpowers/specs/task-add-am-pause-and-am-df0e9d20-design.md` (prepended above). Spec of record: `docs/superpowers/specs/2026-09-27-live-control-design.md` on branch `docs/live-control` (C3, C6, C8-C12).

## Global Constraints

- Change only `src/agent_manager/cli.py`; test only `tests/test_cli.py`. Do not touch `orchestrate.py`, `control.py`, `store.py` or the schema.
- Do not wire Lease/`controlled` into `run_card` or `_resume_from_checkpoint` (sibling 9f5467e3, C11).
- No new runtime dependency. SQLite is the only channel (C1): no socket, fifo or signal handler.
- `run_controls`/`run_leases` stay row-only: nothing journals or rebuilds them.
- CLI envelope unchanged: `{"ok": true, "data": ...}` at exit 0, `{"ok": false, "error": {"type", "message"}}` at exit 3 (`EXIT_ERROR`), `--pretty` supported.
- New refusals are `cli.CliError` subclasses: `NotRunningError`, `DeadRunError`, `NotAcceptingError`, `RunIsLiveError`.
- C8 refusal order: unknown run -> `UnknownRunError`; not `started` -> `NotRunningError`; no live lease -> `DeadRunError`; `accepting=0` -> `NotAcceptingError`; then idempotence.
- C9 message: `run <id> was cancelled; start new work with `am run --milestone`` (`NotResumableError`), both workflows.
- C10 message: `run <id> is still running in pid <pid> on <host> (heartbeat <n>s ago); wait for it to exit, or `am status <id>`` (`RunIsLiveError`), read-only, before any write.
- C12 `control` shape exactly: `{"lease": {"pid","host","acquired_at","heartbeat_at","accepting","live"} | null, "requests": [{"command","requested_at","handled_at"}]}`; requests cover every life in seq order.
- No test sleeps. A "second process" in tests is a second `store.open_db` connection. Liveness is deterministic: the clock is frozen by monkeypatching `cli._utcnow`, and heartbeats are planted relative to it.
- The branch `m9/task-add-am-pause-and-am-df0e9d20` is cut from `m9/task-park-milestone-runs-on-0e1edf31`; the store/control layer (`store.run_status`, `read_lease`, `control_requests`, `immediate`, `add_control`, `LeaseRow`, `ControlRow`, `control.lease_is_live`) is already present there. Assume no other subtask's code.
- Verification: `uv run pytest`.

## Review Focus

- A fresh heartbeat from another host whose pid is meaningless here must count as live (C2: another host, fresh beat) and accept the request. Pinned in Task 1 (`fresh-lease-on-another-host` case).
- A heartbeat exactly `LEASE_STALE_SECONDS` (30s) old is still live (C2 boundary is inclusive); an operator pausing right at the edge must not be told the run is dead. Pinned in Task 1 (`heartbeat-on-the-boundary` case).
- `request_control` called programmatically with a command other than `pause`/`cancel` must be a `ValueError` (in `HANDLED`) and write nothing, never a raw `sqlite3.IntegrityError` from the table's CHECK. Pinned in Task 1.
- `am status` with no RUN_ID (the most-recent-run default) must carry the same `control` key. Pinned in Task 3.
- A cancelled run that still holds a live lease must be refused by `am resume` as cancelled (C9 before C10), so the operator gets the "start new work" instruction. Pinned in Task 4 (`live-lease` case).

---

### Task 1: `am pause` / `am cancel` with the C8 refusals

**Files:**
- Modify: `src/agent_manager/cli.py:20-38` (imports), after `:137` (new error classes), end of file after `:1525` (control commands)
- Test: `tests/test_cli.py:15-27` (imports), append after `:5080`

**Interfaces:**
- Consumes: `store_module.open_db(root) -> sqlite3.Connection`, `store_module.immediate(conn)`, `store_module.run_status(conn, run_id) -> str | None`, `store_module.read_lease(conn, run_id) -> LeaseRow | None`, `store_module.control_requests(conn, run_id, *, lease=None) -> list[ControlRow]`, `store_module.add_control(conn, run_id, *, lease, command, requested_at) -> ControlRow`, `control.lease_is_live(lease, *, now) -> bool`.
- Produces:
  - `cli.NotRunningError`, `cli.DeadRunError`, `cli.NotAcceptingError`, `cli.RunIsLiveError` (all `CliError`).
  - `cli.CONTROL_COMMANDS: tuple[str, ...] = ("pause", "cancel")`.
  - `cli._heartbeat_age(lease: store_module.LeaseRow, now: datetime) -> int` (used again in Task 4).
  - `cli._controllable_lease(conn, run_id, *, command, now) -> store_module.LeaseRow`.
  - `cli._record_control(conn, run_id, *, lease, command, now) -> tuple[store_module.ControlRow, bool]` (replaced in Task 2).
  - `cli._effective_command(rows: Sequence[store_module.ControlRow]) -> str`.
  - `cli.request_control(run_id: str, command: str, *, repo_dir: Path, clock: Callable[[], datetime] = _utcnow) -> dict[str, Any]` with keys `run_id, command, effective, requested_at (ISO str), already_requested (bool), message`.
  - Typer commands `pause` and `cancel`.
  - Test helpers in `tests/test_cli.py`: `CONTROL_RUN_ID`, `CONTROL_NOW`, `HERE`, `CONTROL_KEYS`, `_at`, `_freeze_clock`, `_plant_run`, `_plant_lease`, `_plant_control`, `_controls`, `_lease`, `_invoke_control`.

- [ ] **Step 1: Add the test-file imports**

In `tests/test_cli.py`, change line 21-24 region so the stdlib imports read:

```python
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone
```

(`socket` is inserted after `shutil`; `timedelta` is added to the existing `from datetime import datetime, timezone` line.)

- [ ] **Step 2: Write the failing tests (spec tests 1-5, 9 and the Review Focus cases)**

Append to the end of `tests/test_cli.py`:

```python
# -- live control: am pause / am cancel / status control / resume guard -------
#
# Live control spec section 7 puts CLI refusals, idempotence, status and the
# resume guard here. Runs, leases and requests are planted straight into the
# projection through `store_module.open_db`, which is exactly how a second
# `am` process reaches them. No sleeps: `cli._utcnow` is frozen and every
# heartbeat is planted relative to it.

CONTROL_RUN_ID = "20260929T090000Z-cbe34d00"
CONTROL_NOW = datetime(2026, 9, 29, 9, 0, tzinfo=timezone.utc)
HERE = socket.gethostname()
CONTROL_KEYS = {
    "run_id",
    "command",
    "effective",
    "requested_at",
    "already_requested",
    "message",
}


def _at(seconds: float) -> datetime:
    return CONTROL_NOW + timedelta(seconds=seconds)


def _freeze_clock(monkeypatch, at: datetime = CONTROL_NOW) -> None:
    """Every `cli._utcnow()` call site reads the module global at call time."""
    monkeypatch.setattr(cli, "_utcnow", lambda: at)


def _plant_run(root: Path, *, status: str = "started", workflow: str = "task") -> None:
    if workflow == "task":
        _record(root, CONTROL_RUN_ID, started_at=RECORDED_AT, status=status, with_phases=False)
    else:
        _record_milestone(root, CONTROL_RUN_ID, status=status, workflow=workflow)


def _plant_lease(
    root: Path,
    *,
    token: str = "life-2",
    pid: int | None = None,
    host: str | None = None,
    heartbeat_at: datetime = CONTROL_NOW,
    accepting: bool = True,
) -> None:
    """A `run_leases` row, as another process's `Lease` would have left it.

    Defaults to this process on this host with a heartbeat at the frozen
    clock: live by C2.
    """
    conn = store_module.open_db(cli.resolve_repo_dir(root))
    try:
        conn.execute(
            "INSERT INTO run_leases (run_id, token, pid, host, acquired_at,"
            " heartbeat_at, accepting) VALUES (?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(run_id) DO UPDATE SET token=excluded.token,"
            " pid=excluded.pid, host=excluded.host, acquired_at=excluded.acquired_at,"
            " heartbeat_at=excluded.heartbeat_at, accepting=excluded.accepting",
            (
                CONTROL_RUN_ID,
                token,
                os.getpid() if pid is None else pid,
                HERE if host is None else host,
                _at(-60).isoformat(),
                heartbeat_at.isoformat(),
                int(accepting),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _plant_control(
    root: Path,
    *,
    lease: str,
    command: str,
    requested_at: datetime,
    handled_at: datetime | None = None,
) -> None:
    conn = store_module.open_db(cli.resolve_repo_dir(root))
    try:
        with store_module.immediate(conn):
            row = store_module.add_control(
                conn, CONTROL_RUN_ID, lease=lease, command=command, requested_at=requested_at
            )
            if handled_at is not None:
                conn.execute(
                    "UPDATE run_controls SET handled_at = ? WHERE run_id = ? AND seq = ?",
                    (handled_at.isoformat(), CONTROL_RUN_ID, row.seq),
                )
    finally:
        conn.close()


def _controls(root: Path) -> list[tuple[str, str]]:
    """Every `run_controls` row of the run as `(lease, command)`, in seq order."""
    conn = store_module.open_db(cli.resolve_repo_dir(root))
    try:
        return [
            (row.lease, row.command)
            for row in store_module.control_requests(conn, CONTROL_RUN_ID)
        ]
    finally:
        conn.close()


def _lease(root: Path) -> store_module.LeaseRow | None:
    conn = store_module.open_db(cli.resolve_repo_dir(root))
    try:
        return store_module.read_lease(conn, CONTROL_RUN_ID)
    finally:
        conn.close()


def _invoke_control(root: Path, command: str, run_id: str = CONTROL_RUN_ID, *extra: str):
    return runner.invoke(cli.app, [command, run_id, "--repo-dir", str(root), *extra])


@pytest.mark.parametrize("command", ["pause", "cancel"])
def test_a_request_to_an_unknown_run_is_refused(projection, monkeypatch, command):
    """Spec test 1."""
    _freeze_clock(monkeypatch)

    result = _invoke_control(projection, command, "no-such-run")

    assert result.exit_code == cli.EXIT_ERROR, result.output
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is False
    assert envelope["error"]["type"] == "UnknownRunError"
    assert "no-such-run" in envelope["error"]["message"]
    assert not (paths.data_dir() / "runs" / "no-such-run").exists()


@pytest.mark.parametrize("status", ["stopped", "escalated", "done", "cancelled"])
@pytest.mark.parametrize("command", ["pause", "cancel"])
def test_a_request_to_a_run_that_is_not_started_is_refused_and_names_its_status(
    projection, monkeypatch, command, status
):
    """Spec test 2. C8 order: the status is judged before the lease, so a live
    lease left on the row does not turn this into a different refusal."""
    _freeze_clock(monkeypatch)
    _plant_run(projection, status=status)
    _plant_lease(projection)

    result = _invoke_control(projection, command)

    assert result.exit_code == cli.EXIT_ERROR, result.output
    error = json.loads(result.stdout)["error"]
    assert error["type"] == "NotRunningError"
    assert status in error["message"]
    assert "am status" in error["message"]
    assert _controls(projection) == []


@pytest.mark.parametrize(
    "lease, expected",
    [
        (None, ["no process holds its lease"]),
        (
            {"heartbeat_at": CONTROL_NOW - timedelta(seconds=31)},
            [f"pid {os.getpid()}", HERE, "31s ago"],
        ),
        ({"pid": 0}, ["pid 0", HERE, "0s ago"]),
    ],
    ids=["no-lease", "stale-heartbeat", "dead-pid-on-this-host"],
)
def test_a_request_to_a_started_run_with_no_live_lease_is_refused(
    projection, monkeypatch, lease, expected
):
    """Spec test 3: nobody is left to honour the request, so none is recorded."""
    _freeze_clock(monkeypatch)
    _plant_run(projection)
    if lease is not None:
        _plant_lease(projection, **lease)

    result = _invoke_control(projection, "pause")

    assert result.exit_code == cli.EXIT_ERROR, result.output
    error = json.loads(result.stdout)["error"]
    assert error["type"] == "DeadRunError"
    for piece in expected:
        assert piece in error["message"]
    assert CONTROL_RUN_ID in error["message"]
    assert _controls(projection) == []


@pytest.mark.parametrize("command", ["pause", "cancel"])
def test_a_request_to_a_run_whose_window_has_closed_is_refused(
    projection, monkeypatch, command
):
    """Spec test 4 (C3): a live lease with `accepting=0` is finishing."""
    _freeze_clock(monkeypatch)
    _plant_run(projection)
    _plant_lease(projection, accepting=False)

    result = _invoke_control(projection, command)

    assert result.exit_code == cli.EXIT_ERROR, result.output
    error = json.loads(result.stdout)["error"]
    assert error["type"] == "NotAcceptingError"
    assert CONTROL_RUN_ID in error["message"]
    assert _controls(projection) == []


@pytest.mark.parametrize(
    "command, lease",
    [
        ("pause", {}),
        ("cancel", {}),
        ("pause", {"heartbeat_at": CONTROL_NOW - timedelta(seconds=30)}),
        ("pause", {"pid": 0, "host": "am-test-other-host.invalid"}),
    ],
    ids=["pause", "cancel", "heartbeat-on-the-boundary", "fresh-lease-on-another-host"],
)
def test_a_request_to_a_live_accepting_run_is_recorded_under_its_lease(
    projection, monkeypatch, command, lease
):
    """Spec test 5, plus Review Focus: C2's 30s boundary is inclusive, and a
    fresh heartbeat from another host is live whatever its pid."""
    _freeze_clock(monkeypatch)
    _plant_run(projection)
    _plant_lease(projection, **lease)

    result = _invoke_control(projection, command)

    assert result.exit_code == 0, result.output
    assert "\n" not in result.stdout.strip()
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is True
    data = envelope["data"]
    assert set(data) == CONTROL_KEYS
    assert {key: data[key] for key in CONTROL_KEYS - {"message"}} == {
        "run_id": CONTROL_RUN_ID,
        "command": command,
        "effective": command,
        "requested_at": CONTROL_NOW.isoformat(),
        "already_requested": False,
    }
    assert CONTROL_RUN_ID in data["message"]
    assert _controls(projection) == [("life-2", command)]


def test_request_control_stamps_the_row_with_the_injected_clock(projection):
    _plant_run(projection)
    _plant_lease(projection, heartbeat_at=_at(100))

    data = cli.request_control(
        CONTROL_RUN_ID, "pause", repo_dir=projection, clock=lambda: _at(110)
    )

    assert data["requested_at"] == _at(110).isoformat()
    assert _controls(projection) == [("life-2", "pause")]


def test_request_control_refuses_a_command_it_does_not_know_and_records_nothing(
    projection,
):
    """Review Focus: a `ValueError` (in `HANDLED`), never the table CHECK's
    `sqlite3.IntegrityError`."""
    _plant_run(projection)
    _plant_lease(projection)

    with pytest.raises(ValueError, match="'resume'"):
        cli.request_control(
            CONTROL_RUN_ID, "resume", repo_dir=projection, clock=lambda: CONTROL_NOW
        )

    assert _controls(projection) == []


@pytest.mark.parametrize("command", ["pause", "cancel"])
def test_pause_and_cancel_pretty_indent_the_same_envelope(projection, monkeypatch, command):
    """Spec test 9."""
    _freeze_clock(monkeypatch)
    _plant_run(projection)
    _plant_lease(projection)

    result = _invoke_control(projection, command, CONTROL_RUN_ID, "--pretty")

    assert result.exit_code == 0, result.output
    assert "\n  " in result.stdout
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is True
    assert set(envelope["data"]) == CONTROL_KEYS
    assert envelope["data"]["command"] == command

    refusal = _invoke_control(projection, command, "no-such-run", "--pretty")

    assert refusal.exit_code == cli.EXIT_ERROR, refusal.output
    assert "\n  " in refusal.stdout
    refused = json.loads(refusal.stdout)
    assert refused["ok"] is False
    assert refused["error"]["type"] == "UnknownRunError"
```

- [ ] **Step 3: Run the new tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k "request or pretty_indent_the_same_envelope and (pause or cancel)" -v`
Expected: FAIL. The CliRunner tests exit 2 (`No such command 'pause'` / `'cancel'`) instead of 0 or 3, and the direct calls fail with `AttributeError: module 'agent_manager.cli' has no attribute 'request_control'`.

- [ ] **Step 4: Import `sqlite3` and `control` in `cli.py`**

In `src/agent_manager/cli.py`, change the import block at lines 20-38 to:

```python
import asyncio
import json
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

import typer

from agent_manager import (
    board,
    census,
    control,
    dag,
    dispatch,
    models,
    prompt,
    store as store_module,
)
```

(`control` imports only `store`, `runtime.stop` and the stdlib, never `cli`, so this adds no cycle.)

- [ ] **Step 5: Add the four error classes**

In `src/agent_manager/cli.py`, directly after the `CheckpointMismatchError` class (ends at line 137, before `def resolve_repo_dir`), insert:

```python
class NotRunningError(CliError):
    """`am pause`/`am cancel` was asked to steer a run that is not `started` (C8).

    The message names the recorded status and what to run instead, because
    a stopped run wants `am resume` and a finished one wants nothing.
    """


class DeadRunError(CliError):
    """The run is recorded `started`, but no live process holds its lease (C2, C8).

    Nobody is left to honour a request, so none is recorded. The message
    names the lease's pid, host and heartbeat age, or says there is no lease.
    """


class NotAcceptingError(CliError):
    """The run's control window has closed: it is finishing (C3, C8)."""


class RunIsLiveError(CliError):
    """`am resume` was asked for a run another live process still holds (C10)."""
```

- [ ] **Step 6: Add `request_control` and the two commands**

Append to the end of `src/agent_manager/cli.py` (after line 1525):

```python


CONTROL_COMMANDS: tuple[str, ...] = ("pause", "cancel")
"""What `am pause` and `am cancel` record, weakest first (live control C6)."""


def _heartbeat_age(lease: store_module.LeaseRow, now: datetime) -> int:
    """Whole seconds since `lease` last beat, for a refusal message."""
    return int((now - lease.heartbeat_at).total_seconds())


def _controllable_lease(
    conn: sqlite3.Connection, run_id: str, *, command: str, now: datetime
) -> store_module.LeaseRow:
    """The lease a request to `run_id` is addressed to, or C8's refusal.

    The order is C8's: unknown run, not `started`, no live lease, window
    closed. Runs inside `request_control`'s transaction, so a refusal rolls
    back and leaves no row.
    """
    status = store_module.run_status(conn, run_id)
    if status is None:
        raise UnknownRunError(
            f"run {run_id!r} is not in the projection"
            " (`agent-manager runs` lists the ones that are)"
        )
    if status != "started":
        raise NotRunningError(
            f"run {run_id} is {status}, not started, so there is nothing to {command};"
            f" `am status {run_id}` shows it, and `am resume {run_id}` continues a"
            " stopped or escalated run"
        )
    lease = store_module.read_lease(conn, run_id)
    if lease is None:
        raise DeadRunError(
            f"run {run_id} is recorded started but no process holds its lease;"
            f" it is not running, so `am resume {run_id}` picks it up"
        )
    if not control.lease_is_live(lease, now=now):
        raise DeadRunError(
            f"run {run_id} is not running: its lease is held by pid {lease.pid}"
            f" on {lease.host}, last heartbeat {_heartbeat_age(lease, now)}s ago;"
            f" `am resume {run_id}` picks it up"
        )
    if not lease.accepting:
        raise NotAcceptingError(
            f"run {run_id} is finishing and no longer accepts pause or cancel;"
            f" `am status {run_id}` shows how it ends"
        )
    return lease


def _record_control(
    conn: sqlite3.Connection,
    run_id: str,
    *,
    lease: store_module.LeaseRow,
    command: str,
    now: datetime,
) -> tuple[store_module.ControlRow, bool]:
    """Record `command` for this life of the run; the flag says it was already there."""
    row = store_module.add_control(
        conn, run_id, lease=lease.token, command=command, requested_at=now
    )
    return row, False


def _effective_command(rows: Sequence[store_module.ControlRow]) -> str:
    """The strongest command recorded for one life: `cancel` beats `pause` (C6)."""
    return "cancel" if any(row.command == "cancel" for row in rows) else "pause"


def _control_message(run_id: str, command: str, *, effective: str, already: bool) -> str:
    if already:
        return (
            f"{command} was already requested for run {run_id};"
            f" the effective request is {effective}"
        )
    if command == "pause":
        return (
            f"pause requested for run {run_id}; it parks at its next phase"
            f" boundary, and `am resume {run_id}` continues it"
        )
    return (
        f"cancel requested for run {run_id}; it stops at its next phase"
        " boundary and cannot be resumed"
    )


def request_control(
    run_id: str,
    command: str,
    *,
    repo_dir: Path,
    clock: Callable[[], datetime] = _utcnow,
) -> dict[str, Any]:
    """Record `am pause` or `am cancel` for the process holding `run_id` (C8).

    One `BEGIN IMMEDIATE` transaction covers the refusals, the idempotence
    check and the insert, so two requesters cannot both insert and a refusal
    leaves no row. The process holding the lease applies the request at its
    next poll; this function only records it. SQLite is the only channel (C1).
    """
    if command not in CONTROL_COMMANDS:
        raise ValueError(
            f"unknown control command {command!r};"
            f" expected one of {', '.join(CONTROL_COMMANDS)}"
        )
    root = resolve_repo_dir(repo_dir)
    now = clock()
    conn = store_module.open_db(root)
    try:
        with store_module.immediate(conn):
            lease = _controllable_lease(conn, run_id, command=command, now=now)
            row, already = _record_control(
                conn, run_id, lease=lease, command=command, now=now
            )
            effective = _effective_command(
                store_module.control_requests(conn, run_id, lease=lease.token)
            )
    finally:
        conn.close()
    return {
        "run_id": run_id,
        "command": command,
        "effective": effective,
        "requested_at": row.requested_at.isoformat(),
        "already_requested": already,
        "message": _control_message(run_id, command, effective=effective, already=already),
    }


def _control(command: str, run_id: str, *, repo_dir: Path, pretty: bool) -> None:
    """`resume`'s envelope pattern for `pause` and `cancel`.

    `clock=_utcnow` reads the module global at call time, so a test that
    freezes `cli._utcnow` freezes this command too.
    """
    try:
        payload = request_control(run_id, command, repo_dir=repo_dir, clock=_utcnow)
    except HANDLED as error:
        typer.echo(render(error_envelope(error), pretty=pretty))
        raise typer.Exit(EXIT_ERROR) from None
    typer.echo(render(ok_envelope(payload), pretty=pretty))


@app.command("pause")
def pause(
    run_id: str = typer.Argument(..., metavar="RUN_ID", help="The running run to park."),
    repo_dir: Path = typer.Option(
        Path("."), "--repo-dir", help="The repository whose projection is written."
    ),
    pretty: bool = typer.Option(False, "--pretty", help="Indent the JSON envelope."),
) -> None:
    """Ask a running run to park at its next phase boundary; `am resume` continues it."""
    _control("pause", run_id, repo_dir=repo_dir, pretty=pretty)


@app.command("cancel")
def cancel(
    run_id: str = typer.Argument(..., metavar="RUN_ID", help="The running run to stop."),
    repo_dir: Path = typer.Option(
        Path("."), "--repo-dir", help="The repository whose projection is written."
    ),
    pretty: bool = typer.Option(False, "--pretty", help="Indent the JSON envelope."),
) -> None:
    """Ask a running run to stop at its next phase boundary and close it for good."""
    _control("cancel", run_id, repo_dir=repo_dir, pretty=pretty)
```

- [ ] **Step 7: Run the new tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k "request or pretty_indent_the_same_envelope and (pause or cancel)" -v`
Expected: PASS (all parametrized cases).

- [ ] **Step 8: Run the whole CLI test file**

Run: `uv run pytest tests/test_cli.py -q`
Expected: PASS (no existing test regresses; the import-order test `test_cli_and_orchestrate_import_cleanly_in_either_order` still passes).

- [ ] **Step 9: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "feat(cli): add am pause and am cancel with the C8 refusals"
```

---

### Task 2: Idempotence against this life's requests

**Files:**
- Modify: `src/agent_manager/cli.py` (the `_record_control` function added in Task 1, at the end of the file)
- Test: `tests/test_cli.py` (append after Task 1's tests)

**Interfaces:**
- Consumes: Task 1's `_record_control` signature, `request_control`, and the test helpers `_freeze_clock`, `_plant_run`, `_plant_lease`, `_plant_control`, `_controls`, `_invoke_control`, `_at`, `CONTROL_NOW`.
- Produces: `cli.CONTROL_SUBSUMES: dict[str, tuple[str, ...]]`; `_record_control(conn, run_id, *, lease, command, now) -> tuple[store_module.ControlRow, bool]`, same signature, now returns `(existing_row, True)` for a no-op.

- [ ] **Step 1: Write the failing tests (spec tests 6-8)**

Append to `tests/test_cli.py`:

```python
def test_a_repeated_pause_is_a_no_op_that_reports_the_first_request(projection, monkeypatch):
    """Spec test 6, first half."""
    _plant_run(projection)
    _plant_lease(projection)
    _freeze_clock(monkeypatch)
    assert _invoke_control(projection, "pause").exit_code == 0

    _freeze_clock(monkeypatch, _at(5))
    result = _invoke_control(projection, "pause")

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)["data"]
    assert data["already_requested"] is True
    assert data["effective"] == "pause"
    assert data["requested_at"] == CONTROL_NOW.isoformat()
    assert _controls(projection) == [("life-2", "pause")]


def test_a_pause_after_a_cancel_is_a_no_op_and_the_cancel_stays_effective(
    projection, monkeypatch
):
    """Spec test 6, second half: a pause never weakens a cancel."""
    _plant_run(projection)
    _plant_lease(projection)
    _plant_control(projection, lease="life-2", command="cancel", requested_at=_at(-3))
    _freeze_clock(monkeypatch)

    result = _invoke_control(projection, "pause")

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)["data"]
    assert data["already_requested"] is True
    assert data["effective"] == "cancel"
    assert data["requested_at"] == _at(-3).isoformat()
    assert _controls(projection) == [("life-2", "cancel")]


def test_a_cancel_after_a_pause_upgrades_it_and_a_repeated_cancel_is_a_no_op(
    projection, monkeypatch
):
    """Spec test 7."""
    _plant_run(projection)
    _plant_lease(projection)
    _freeze_clock(monkeypatch)
    assert _invoke_control(projection, "pause").exit_code == 0

    _freeze_clock(monkeypatch, _at(1))
    upgrade = _invoke_control(projection, "cancel")

    assert upgrade.exit_code == 0, upgrade.output
    data = json.loads(upgrade.stdout)["data"]
    assert data["already_requested"] is False
    assert data["effective"] == "cancel"
    assert data["requested_at"] == _at(1).isoformat()
    assert _controls(projection) == [("life-2", "pause"), ("life-2", "cancel")]

    _freeze_clock(monkeypatch, _at(2))
    repeat = _invoke_control(projection, "cancel")

    assert repeat.exit_code == 0, repeat.output
    data = json.loads(repeat.stdout)["data"]
    assert data["already_requested"] is True
    assert data["effective"] == "cancel"
    assert data["requested_at"] == _at(1).isoformat()
    assert _controls(projection) == [("life-2", "pause"), ("life-2", "cancel")]


def test_requests_sent_to_an_earlier_life_do_not_make_a_new_pause_a_no_op(
    projection, monkeypatch
):
    """Spec test 8: a resumed run starts clean (C4)."""
    _plant_run(projection)
    _plant_control(
        projection,
        lease="life-1",
        command="pause",
        requested_at=_at(-300),
        handled_at=_at(-299),
    )
    _plant_control(projection, lease="life-1", command="cancel", requested_at=_at(-200))
    _plant_lease(projection, token="life-2")
    _freeze_clock(monkeypatch)

    result = _invoke_control(projection, "pause")

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)["data"]
    assert data["already_requested"] is False
    assert data["effective"] == "pause"
    assert data["requested_at"] == CONTROL_NOW.isoformat()
    assert _controls(projection) == [
        ("life-1", "pause"),
        ("life-1", "cancel"),
        ("life-2", "pause"),
    ]
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k "no_op or earlier_life" -v`
Expected: FAIL for `test_a_repeated_pause_is_a_no_op_that_reports_the_first_request`, `test_a_pause_after_a_cancel_is_a_no_op_and_the_cancel_stays_effective` and `test_a_cancel_after_a_pause_upgrades_it_and_a_repeated_cancel_is_a_no_op` (`assert False is True` on `already_requested`, because Task 1 always inserts). `test_requests_sent_to_an_earlier_life_do_not_make_a_new_pause_a_no_op` already PASSES here (Task 1 always inserts, and `effective` reads only this life's rows); it is the guard that Step 3's idempotence reads only `lease.token`'s rows.

- [ ] **Step 3: Make `_record_control` idempotent**

In `src/agent_manager/cli.py`, replace the whole `_record_control` function added in Task 1 with:

```python
CONTROL_SUBSUMES: dict[str, tuple[str, ...]] = {
    "pause": ("pause", "cancel"),
    "cancel": ("cancel",),
}
"""Requests already recorded that make a new one a no-op (C8). A pause is
covered by any pause or cancel, a cancel only by a cancel, so a cancel after a
pause is recorded and upgrades it."""


def _record_control(
    conn: sqlite3.Connection,
    run_id: str,
    *,
    lease: store_module.LeaseRow,
    command: str,
    now: datetime,
) -> tuple[store_module.ControlRow, bool]:
    """Record `command` for this life of the run; the flag says it was already there.

    Only rows addressed to `lease.token` count, so a request sent to an
    earlier life never makes one to a resumed run a no-op. A no-op returns
    the first row that covers it, whose time is reported as `requested_at`.
    """
    for row in store_module.control_requests(conn, run_id, lease=lease.token):
        if row.command in CONTROL_SUBSUMES[command]:
            return row, True
    row = store_module.add_control(
        conn, run_id, lease=lease.token, command=command, requested_at=now
    )
    return row, False
```

- [ ] **Step 4: Run the new tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k "no_op or earlier_life or request or pretty_indent_the_same_envelope" -v`
Expected: PASS (Task 1's tests included).

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "feat(cli): make am pause and am cancel idempotent per lease"
```

---

### Task 3: `control` in `am status`

**Files:**
- Modify: `src/agent_manager/cli.py:255-268` (`status_payload`), before it (new `control_view`), `:1174-1202` (`status_for`)
- Test: `tests/test_cli.py` (append after Task 2's tests)

**Interfaces:**
- Consumes: `store_module.read_lease`, `store_module.control_requests`, `control.lease_is_live`, `_utcnow`; test helpers from Task 1 and `_pure_run` (tests/test_cli.py:133).
- Produces:
  - `cli.control_view(lease: store_module.LeaseRow | None, requests: Sequence[store_module.ControlRow], *, now: datetime) -> dict[str, Any]`.
  - `cli.status_payload(run: models.Run, control: dict[str, Any] | None = None) -> dict[str, Any]` whose result always has a `control` key.

- [ ] **Step 1: Write the failing tests (spec test 10 and the Review Focus default-run case)**

Append to `tests/test_cli.py`:

```python
def test_the_status_payload_defaults_to_an_empty_control():
    assert cli.status_payload(_pure_run([]))["control"] == {"lease": None, "requests": []}


def test_status_of_a_run_with_no_lease_shows_an_empty_control(projection, monkeypatch):
    """Spec test 10, first half; Review Focus: the no-RUN_ID default too."""
    _freeze_clock(monkeypatch)
    _plant_run(projection)

    for args in (["status", CONTROL_RUN_ID], ["status"]):
        result = runner.invoke(cli.app, [*args, "--repo-dir", str(projection)])

        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout)["data"]["control"] == {
            "lease": None,
            "requests": [],
        }


@pytest.mark.parametrize(
    "heartbeat_at, live",
    [
        (CONTROL_NOW - timedelta(seconds=5), True),
        (CONTROL_NOW - timedelta(seconds=31), False),
    ],
    ids=["live", "stale"],
)
def test_status_shows_the_lease_and_every_lifes_requests_in_seq_order(
    projection, monkeypatch, heartbeat_at, live
):
    """Spec test 10, second half: C12's exact shape, `live` worked out at read
    time, and `status` stays read-only."""
    _freeze_clock(monkeypatch)
    _plant_run(projection)
    _plant_control(
        projection,
        lease="life-1",
        command="pause",
        requested_at=_at(-300),
        handled_at=_at(-299),
    )
    _plant_control(projection, lease="life-1", command="cancel", requested_at=_at(-200))
    _plant_control(projection, lease="life-2", command="pause", requested_at=_at(-10))
    _plant_lease(projection, heartbeat_at=heartbeat_at)
    before = (_controls(projection), _lease(projection))

    for args in (["status", CONTROL_RUN_ID], ["status"]):
        result = runner.invoke(cli.app, [*args, "--repo-dir", str(projection)])

        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout)["data"]["control"] == {
            "lease": {
                "pid": os.getpid(),
                "host": HERE,
                "acquired_at": _at(-60).isoformat(),
                "heartbeat_at": heartbeat_at.isoformat(),
                "accepting": True,
                "live": live,
            },
            "requests": [
                {
                    "command": "pause",
                    "requested_at": _at(-300).isoformat(),
                    "handled_at": _at(-299).isoformat(),
                },
                {
                    "command": "cancel",
                    "requested_at": _at(-200).isoformat(),
                    "handled_at": None,
                },
                {
                    "command": "pause",
                    "requested_at": _at(-10).isoformat(),
                    "handled_at": None,
                },
            ],
        }
    assert (_controls(projection), _lease(projection)) == before
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k "empty_control or every_lifes_requests" -v`
Expected: FAIL with `KeyError: 'control'`.

- [ ] **Step 3: Add `control_view` and the `control` key to `status_payload`**

In `src/agent_manager/cli.py`, replace the whole `status_payload` function (lines 255-268) with:

```python
def control_view(
    lease: store_module.LeaseRow | None,
    requests: Sequence[store_module.ControlRow],
    *,
    now: datetime,
) -> dict[str, Any]:
    """C12's `control` key: the lease or `None`, and every life's requests in seq order.

    `live` is worked out here, at read time, by `control.lease_is_live`; it
    is never stored. Timestamps are ISO strings.
    """
    return {
        "lease": None
        if lease is None
        else {
            "pid": lease.pid,
            "host": lease.host,
            "acquired_at": lease.acquired_at.isoformat(),
            "heartbeat_at": lease.heartbeat_at.isoformat(),
            "accepting": lease.accepting,
            "live": control.lease_is_live(lease, now=now),
        },
        "requests": [
            {
                "command": row.command,
                "requested_at": row.requested_at.isoformat(),
                "handled_at": None if row.handled_at is None else row.handled_at.isoformat(),
            }
            for row in requests
        ],
    }


def status_payload(
    run: models.Run, control: dict[str, Any] | None = None
) -> dict[str, Any]:
    """The run's identity, the §9 tree, the flat table over it, and live control.

    `model_dump()` rather than `model_dump(mode="json")`: the payload keeps its
    `Path` and `datetime` objects and `render`'s `default=str` stringifies them
    once, at the edge, the same way `run_card`'s `worktree` is handled. Field
    names are `models.py`'s and are not renamed for display. `control` is
    `control_view`'s result; `None` renders as no lease and no requests, so
    the key is always present (C12).
    """
    tree = run.model_dump()
    return {
        "run": {field: tree[field] for field in RUN_IDENTITY},
        "stories": tree["stories"],
        "rows": status_rows(run),
        "control": {"lease": None, "requests": []} if control is None else control,
    }
```

(The `control` parameter shadows the `control` module only inside `status_payload`, which never uses the module.)

- [ ] **Step 4: Read the control rows in `status_for`**

In `src/agent_manager/cli.py`, in `status_for` (starts at line 1174 before this task's edits), replace:

```python
        return status_payload(run)
    finally:
        conn.close()
```

with:

```python
        state = control_view(
            store_module.read_lease(conn, wanted),
            store_module.control_requests(conn, wanted),
            now=_utcnow(),
        )
        return status_payload(run, state)
    finally:
        conn.close()
```

and add this sentence to the end of the `status_for` docstring: `The lease and every control request are read on the same connection and rendered by `control_view`, still without a write.`

- [ ] **Step 5: Run the new tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k "empty_control or every_lifes_requests or status" -v`
Expected: PASS (existing status tests included).

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "feat(cli): show the lease and control requests in am status"
```

---

### Task 4: Guard `am resume` against cancelled and live runs

**Files:**
- Modify: `src/agent_manager/cli.py:1407-1448` (`resume_run`, before this plan's insertions shift it)
- Test: `tests/test_cli.py` (append after Task 3's tests)

**Interfaces:**
- Consumes: `NotResumableError` (cli.py:119), `RunIsLiveError` and `_heartbeat_age` (Task 1), `store_module.read_lease`, `control.lease_is_live`, `_utcnow`; test helpers from Task 1 plus `_Forbidden` (tests/test_cli.py:2555), `_runs_snapshot` (:3780), `_attempt_rows` (:3794), `_record_milestone` (:4853).
- Produces: `resume_run` refuses before any `Store.open`, `_resume_from_checkpoint` or `orchestrate.run_milestone` call.

- [ ] **Step 1: Write the failing tests (spec tests 11-12 and the Review Focus cancelled-and-live case)**

Append to `tests/test_cli.py`:

```python
def _forbid_resume(monkeypatch) -> None:
    monkeypatch.setattr(cli, "Store", _Forbidden("Store"))
    monkeypatch.setattr(cli, "_resume_from_checkpoint", _Forbidden("_resume_from_checkpoint"))
    monkeypatch.setattr(orchestrate, "run_milestone", _Forbidden("orchestrate.run_milestone"))


def _resume_guard_state(root: Path) -> tuple:
    return (_runs_snapshot(), _attempt_rows(root), _controls(root), _lease(root))


@pytest.mark.parametrize("leased", [False, True], ids=["no-lease", "live-lease"])
@pytest.mark.parametrize("workflow", ["task", "milestone"])
def test_resume_refuses_a_cancelled_run_and_writes_nothing(
    projection, monkeypatch, workflow, leased
):
    """Spec test 11 (C9), both workflows. Review Focus: a cancelled run that
    still holds a live lease is refused as cancelled."""
    _freeze_clock(monkeypatch)
    _plant_run(projection, status="cancelled", workflow=workflow)
    if leased:
        _plant_lease(projection)
    before = _resume_guard_state(projection)
    _forbid_resume(monkeypatch)

    result = runner.invoke(cli.app, ["resume", CONTROL_RUN_ID, "--repo-dir", str(projection)])

    assert result.exit_code == cli.EXIT_ERROR, result.output
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is False
    assert envelope["error"] == {
        "type": "NotResumableError",
        "message": (
            f"run {CONTROL_RUN_ID} was cancelled;"
            " start new work with `am run --milestone`"
        ),
    }
    assert _resume_guard_state(projection) == before


@pytest.mark.parametrize("workflow", ["task", "milestone"])
def test_resume_refuses_a_run_whose_lease_is_live_and_writes_nothing(
    projection, monkeypatch, workflow
):
    """Spec test 12 (C10), both workflows."""
    _freeze_clock(monkeypatch)
    _plant_run(projection, status="started", workflow=workflow)
    _plant_lease(projection, heartbeat_at=_at(-5))
    before = _resume_guard_state(projection)
    _forbid_resume(monkeypatch)

    result = runner.invoke(cli.app, ["resume", CONTROL_RUN_ID, "--repo-dir", str(projection)])

    assert result.exit_code == cli.EXIT_ERROR, result.output
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is False
    assert envelope["error"] == {
        "type": "RunIsLiveError",
        "message": (
            f"run {CONTROL_RUN_ID} is still running in pid {os.getpid()} on {HERE}"
            " (heartbeat 5s ago); wait for it to exit,"
            f" or `am status {CONTROL_RUN_ID}`"
        ),
    }
    assert _resume_guard_state(projection) == before


def test_resume_is_not_blocked_by_a_dead_lease(projection, monkeypatch):
    """Spec test 12, last clause: a stale lease is a crashed run, which is
    exactly what `resume` is for."""
    _freeze_clock(monkeypatch)
    _plant_run(projection, status="started")
    _plant_lease(projection, heartbeat_at=_at(-31))
    seen: list[str] = []

    def fake_resume(run, **kwargs):
        seen.append(run.id)
        return {"status": "done"}

    monkeypatch.setattr(cli, "_resume_from_checkpoint", fake_resume)
    monkeypatch.setattr(orchestrate, "run_milestone", _Forbidden("orchestrate.run_milestone"))

    result = runner.invoke(cli.app, ["resume", CONTROL_RUN_ID, "--repo-dir", str(projection)])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == cli.ok_envelope({"status": "done"})
    assert seen == [CONTROL_RUN_ID]
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k "resume_refuses_a_cancelled_run or lease_is_live or not_blocked_by_a_dead_lease" -v`
Expected: FAIL for the cancelled-run and live-lease tests with `Failed: the milestone dry run reached cli._resume_from_checkpoint` (task workflow) or `... reached cli.orchestrate.run_milestone` (milestone workflow), since `resume_run` has no guard yet. `test_resume_is_not_blocked_by_a_dead_lease` already PASSES; it is the guard that Step 3 does not over-refuse.

- [ ] **Step 3: Add the two guards to `resume_run`**

In `src/agent_manager/cli.py`, in `resume_run`, replace:

```python
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
```

with:

```python
    root = resolve_repo_dir(repo_dir)
    conn = store_module.open_db(root)
    try:
        run = store_module.load_run(conn, run_id)
        if run is None:
            raise UnknownRunError(
                f"run {run_id!r} is not in the projection for {root}"
                " (`agent-manager runs` lists the ones that are)"
            )
        # C9, then C10: both read-only and before anything is written, so a
        # refusal leaves no run directory, row or journal line behind.
        if run.status == "cancelled":
            raise NotResumableError(
                f"run {run.id} was cancelled; start new work with `am run --milestone`"
            )
        lease = store_module.read_lease(conn, run.id)
        now = _utcnow()
        if lease is not None and control.lease_is_live(lease, now=now):
            raise RunIsLiveError(
                f"run {run.id} is still running in pid {lease.pid} on {lease.host}"
                f" (heartbeat {_heartbeat_age(lease, now)}s ago); wait for it to exit,"
                f" or `am status {run.id}`"
            )
    finally:
        conn.close()
```

Then append this paragraph to the end of the `resume_run` docstring: `A cancelled run is refused for both workflows (live control C9), and so is a run whose lease is still live (C10): both refusals read only the connection that loaded the run.`

- [ ] **Step 4: Run the new tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k "resume" -v`
Expected: PASS (existing resume tests included).

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "feat(cli): refuse am resume of a cancelled or live run"
```

---

### Task 5: Full verification

**Files:**
- None modified unless the suite finds a regression.

**Interfaces:**
- Consumes: everything above.
- Produces: a green `uv run pytest`.

- [ ] **Step 1: Run the full suite**

Run: `uv run pytest`
Expected: PASS, with no new skips beyond the existing `requires_git`/`requires_brd` ones on a machine without those tools.

- [ ] **Step 2: If a resume test elsewhere fails with `RunIsLiveError`**

That means a test left a `run_leases` row with this process's pid and a fresh heartbeat (a `Lease` that did not release). Do not weaken the guard: find the test and confirm its `Lease` block exits (`control.Lease.__exit__` releases on any exit). Re-run `uv run pytest` until green.

- [ ] **Step 3: Confirm the scope**

Run: `git diff --stat m9/task-park-milestone-runs-on-0e1edf31...HEAD`
Expected: only `src/agent_manager/cli.py`, `tests/test_cli.py`, and the spec/plan docs under `docs/superpowers/` are listed.
