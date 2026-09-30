"""Live control's lease, heartbeat and request watcher (live-control spec C1-C4).

Steps tier of spec §14: a real temporary SQLite database and journal opened
through `store.Store.open`, no network, no harness dispatch, so these run in
the default `uv run pytest` suite and not under `tests/e2e/`. A "second
process" is a second `store.open_db` connection.

No test sleeps to prove ordering: `_until` yields to the loop under a time
bound, and threads synchronise on a `threading.Event`.

Adaptation: the card's excerpts name an `opened_store` fixture and
`root`/`_send`/`_within`/`_until` helpers that did not exist. They are
defined here, after `tests/test_store.py`'s `repo` + `Store.open` pattern.
"""

from __future__ import annotations

import ast
import asyncio
import itertools
import os
import socket
import sqlite3
import sys
import threading
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, TypeVar

import pytest

from agent_manager import control, models, store
from agent_manager.runtime.stop import StopSignal

RUN_ID = "run-2026-09-27-01"
HEARTBEAT_THREAD = "am-lease-heartbeat"

T = TypeVar("T")


@pytest.fixture
def root(monkeypatch, tmp_path) -> Path:
    """A redirected data dir plus a stand-in for the project worktree."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = tmp_path / "repo"
    project.mkdir()
    return project


@pytest.fixture
def opened_store(root) -> Iterator[store.Store]:
    st = store.Store.open(root, RUN_ID)
    try:
        yield st
    finally:
        st.close()


def _at(seconds: float) -> datetime:
    return datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc) + timedelta(seconds=seconds)


def _lease_row(*, heartbeat_at: datetime, pid: int = 4242, host: str = "build-box") -> store.LeaseRow:
    return store.LeaseRow(
        run_id=RUN_ID,
        token="t1",
        pid=pid,
        host=host,
        acquired_at=_at(0),
        heartbeat_at=heartbeat_at,
        accepting=True,
    )


def _send(root: Path, token: str, command: str, at: datetime | None = None) -> store.ControlRow:
    """Insert one request from a second connection, as `am pause` would."""
    conn = store.open_db(root)
    try:
        with store.immediate(conn):
            return store.add_control(
                conn, RUN_ID, lease=token, command=command, requested_at=at or _at(0)
            )
    finally:
        conn.close()


def _read_lease(root: Path) -> store.LeaseRow | None:
    conn = store.open_db(root)
    try:
        return store.read_lease(conn, RUN_ID)
    finally:
        conn.close()


def _requests(root: Path) -> list[store.ControlRow]:
    conn = store.open_db(root)
    try:
        return store.control_requests(conn, RUN_ID)
    finally:
        conn.close()


async def _within(awaitable: Awaitable[T], limit: float = 5.0) -> T:
    """Await `awaitable`, failing the test instead of hanging past `limit`."""
    return await asyncio.wait_for(awaitable, limit)


async def _until(predicate: Callable[[], bool], limit: float = 5.0) -> None:
    """Yield to the loop until `predicate()` holds; a bound, not a sleep."""
    async with asyncio.timeout(limit):
        while not predicate():
            await asyncio.sleep(0)


def _heartbeat_threads() -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name == HEARTBEAT_THREAD]


class FakeAgent:
    def __init__(self) -> None:
        self.paused = 0

    def pause(self) -> None:
        self.paused += 1


class Wrapped:
    """A real `Store` with some methods overridden; everything else passes through."""

    def __init__(self, inner: store.Store) -> None:
        self._inner = inner

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


# -- pid_alive and lease_is_live (C2) -----------------------------------------


def test_pid_alive_true_for_self_false_for_missing_pid_true_on_permission_error(monkeypatch):
    assert control.pid_alive(os.getpid()) is True

    def missing(pid: int, sig: int) -> None:
        raise ProcessLookupError(pid)

    monkeypatch.setattr(control.os, "kill", missing)
    assert control.pid_alive(4242) is False

    def forbidden(pid: int, sig: int) -> None:
        raise PermissionError(pid)

    monkeypatch.setattr(control.os, "kill", forbidden)
    assert control.pid_alive(4242) is True


def test_pid_alive_is_false_for_non_positive_pids_without_signalling(monkeypatch):
    # Review Focus 2: kill(0, 0) signals our own process group and succeeds.
    calls: list[int] = []
    monkeypatch.setattr(control.os, "kill", lambda pid, sig: calls.append(pid))
    assert control.pid_alive(0) is False
    assert control.pid_alive(-1) is False
    assert calls == []


def test_lease_is_live_within_stale_window_same_host_live_pid():
    lease = _lease_row(heartbeat_at=_at(0))
    assert control.lease_is_live(lease, now=_at(10), host="build-box", alive=lambda pid: True)


def test_lease_is_stale_past_window():
    lease = _lease_row(heartbeat_at=_at(0))
    assert control.lease_is_live(
        lease, now=_at(control.LEASE_STALE_SECONDS), host="build-box", alive=lambda pid: True
    )
    assert not control.lease_is_live(
        lease,
        now=_at(control.LEASE_STALE_SECONDS + 0.001),
        host="build-box",
        alive=lambda pid: True,
    )
    assert not control.lease_is_live(
        lease, now=_at(5), host="build-box", alive=lambda pid: True, stale_after=4.0
    )


def test_lease_on_same_host_with_dead_pid_is_not_live():
    seen: list[int] = []

    def dead(pid: int) -> bool:
        seen.append(pid)
        return False

    lease = _lease_row(heartbeat_at=_at(0), pid=4242)
    assert not control.lease_is_live(lease, now=_at(1), host="build-box", alive=dead)
    assert seen == [4242]


def test_lease_on_other_host_is_live_regardless_of_pid():
    lease = _lease_row(heartbeat_at=_at(0), host="other-box")
    assert control.lease_is_live(lease, now=_at(1), host="build-box", alive=lambda pid: False)
    assert not control.lease_is_live(
        lease, now=_at(31), host="build-box", alive=lambda pid: False
    )


def test_control_constants_match_the_design():
    assert control.CONTROL_POLL_SECONDS == 1.0
    assert control.HEARTBEAT_SECONDS == 5.0
    assert control.LEASE_STALE_SECONDS == 30.0


def test_control_module_imports_no_cli_orchestrate_or_grafo():
    tree = ast.parse(Path(control.__file__).read_text())
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "control.py uses absolute imports only"
            base = node.module or ""
            if base == "agent_manager":
                modules |= {f"agent_manager.{alias.name}" for alias in node.names}
            else:
                modules.add(base)
    ours = {name for name in modules if name.split(".")[0] == "agent_manager"}
    theirs = {name.split(".")[0] for name in modules} - {"agent_manager"}
    assert ours <= {"agent_manager.store", "agent_manager.runtime.stop"}
    assert theirs <= set(sys.stdlib_module_names) | {"__future__"}


# -- claim keys (multi-process X5) ---------------------------------------------


def test_card_claim_and_branch_claim_name_their_keys():
    card = "ec7ae954-0000-4000-8000-000000000000"
    assert control.card_claim(card) == f"card:{card}"
    assert control.branch_claim("m10/task-lease-with-claims-for-ec7ae954") == (
        "branch:m10/task-lease-with-claims-for-ec7ae954"
    )


# -- Lease ----------------------------------------------------------------------


def test_lease_acquires_on_enter_and_releases_on_exit(root, opened_store):
    with control.Lease(opened_store, pid=4242, host="build-box", clock=lambda: _at(0)) as lease:
        assert len(lease.token) == 32 and int(lease.token, 16) >= 0
        assert _read_lease(root) == store.LeaseRow(
            run_id=RUN_ID,
            token=lease.token,
            pid=4242,
            host="build-box",
            acquired_at=_at(0),
            heartbeat_at=_at(0),
            accepting=True,
        )
    assert _read_lease(root) is None
    assert _heartbeat_threads() == []

    with control.Lease(opened_store) as mine:
        row = _read_lease(root)
        assert row is not None
        assert (row.pid, row.host) == (os.getpid(), socket.gethostname())
    assert mine.token != lease.token


def test_lease_releases_on_exception_and_reraises(root, opened_store):
    with pytest.raises(RuntimeError, match="inside the run"):
        with control.Lease(opened_store):
            assert _read_lease(root) is not None
            raise RuntimeError("inside the run")
    assert _read_lease(root) is None
    assert _heartbeat_threads() == []


def test_lease_close_window_stops_accepting(root, opened_store):
    with control.Lease(opened_store) as lease:
        lease.close_window()
        row = _read_lease(root)
        assert row is not None and row.accepting is False


def test_lease_heartbeat_thread_beats_and_stops_on_exit(root, opened_store):
    ticks = itertools.count()
    beaten = threading.Event()

    class Counting(Wrapped):
        beats = 0

        def beat(self, token: str, now: datetime) -> None:
            self._inner.beat(token, now)
            Counting.beats += 1
            if Counting.beats >= 2:
                beaten.set()

    with control.Lease(Counting(opened_store), heartbeat=0.001, clock=lambda: _at(next(ticks))):
        assert beaten.wait(timeout=5.0)
        threads = _heartbeat_threads()
        assert len(threads) == 1 and threads[0].daemon
        row = _read_lease(root)
        assert row is not None and row.heartbeat_at > row.acquired_at == _at(0)
    assert not threads[0].is_alive()
    assert _read_lease(root) is None


def test_lease_heartbeat_survives_an_operational_error(root, opened_store):
    # Review Focus 3: a locked database must not kill the heartbeat thread.
    recovered = threading.Event()

    class Flaky(Wrapped):
        calls = 0

        def beat(self, token: str, now: datetime) -> None:
            Flaky.calls += 1
            if Flaky.calls == 1:
                raise sqlite3.OperationalError("database is locked")
            self._inner.beat(token, now)
            recovered.set()

    ticks = itertools.count()
    with control.Lease(Flaky(opened_store), heartbeat=0.001, clock=lambda: _at(next(ticks))):
        assert recovered.wait(timeout=5.0)
        assert _heartbeat_threads()[0].is_alive()
    assert _heartbeat_threads() == []


def test_a_lease_fences_its_store_only_while_it_is_held(root, opened_store):
    # Review Focus 5 of the a7ed6c11 plan.
    run = models.Run(
        id=RUN_ID,
        workflow="task",
        repo_dir=root,
        base_branch="main",
        branch_prefix="m10/",
        status="started",
        config=models.RunConfig(),
    )
    with control.Lease(opened_store):
        opened_store.record_run(run)
        thief = store.Store.open(root, RUN_ID)
        try:
            thief.take_lease(
                token="thief", pid=1, host="elsewhere", now=_at(0), is_live=lambda row: False
            )
        finally:
            thief.close()
        with pytest.raises(store.LeaseLostError) as caught:
            opened_store.record_run(run.model_copy(update={"status": "done"}))
        assert caught.value.holder is not None and caught.value.holder.token == "thief"

    # Out of the block the store is unbound and writes as M9 did.
    assert opened_store.record_run(run.model_copy(update={"status": "done"})).event == "run_upsert"


# -- apply_pending (C4) --------------------------------------------------------


def test_apply_pending_requests_each_row_in_order_and_marks_handled(root, opened_store):
    # Review Focus 4: a repeated pause, then cancel.
    stop, agent = StopSignal(), FakeAgent()
    stop.register(agent)
    with control.Lease(opened_store) as lease:
        for command in ("pause", "pause", "cancel"):
            _send(root, lease.token, command)

        applied = control.apply_pending(opened_store, stop, lease.token, clock=lambda: _at(7))

        assert [(row.seq, row.command) for row in applied] == [
            (0, "pause"),
            (1, "pause"),
            (2, "cancel"),
        ]
        assert stop.requested == "cancel" and stop.primary is None
        assert agent.paused == 3
        assert [row.handled_at for row in _requests(root)] == [_at(7)] * 3
        assert control.apply_pending(opened_store, stop, lease.token) == []


def test_a_request_under_an_old_lease_token_is_never_applied(root, opened_store):
    stop = StopSignal()
    with control.Lease(opened_store) as old:
        old_token = old.token
    _send(root, old_token, "cancel")

    with control.Lease(opened_store) as lease:
        assert control.apply_pending(opened_store, stop, lease.token) == []

    assert stop.requested is None and not stop.triggered
    assert [(row.lease, row.handled_at) for row in _requests(root)] == [(old_token, None)]


# -- watch ---------------------------------------------------------------------


async def test_watch_swallows_operational_error_and_keeps_polling(root, opened_store):
    class Locked(Wrapped):
        calls = 0

        def pending_controls(self, token: str) -> list[store.ControlRow]:
            Locked.calls += 1
            if Locked.calls <= 2:
                raise sqlite3.OperationalError("database is locked")
            return self._inner.pending_controls(token)

    stop = StopSignal()
    _send(root, "t1", "pause")
    task = asyncio.create_task(control.watch(Locked(opened_store), stop, "t1", interval=0))
    try:
        await _until(lambda: stop.requested == "pause")
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert Locked.calls >= 3
    assert task.cancelled()

    class Broken(Wrapped):
        def pending_controls(self, token: str) -> list[store.ControlRow]:
            raise RuntimeError("not a lock")

    with pytest.raises(RuntimeError, match="not a lock"):
        await _within(control.watch(Broken(opened_store), StopSignal(), "t1", interval=0))


# -- controlled ----------------------------------------------------------------


async def _blocked_forever(started: asyncio.Event, cancelled: list[bool]) -> str:
    started.set()
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        cancelled.append(True)
        raise
    return "unreachable"


async def test_controlled_returns_work_result_and_applies_a_request_sent_mid_run(
    root, opened_store
):
    stop, agent = StopSignal(), FakeAgent()
    stop.register(agent)
    with control.Lease(opened_store) as lease:

        async def work() -> str:
            _send(root, lease.token, "pause")
            await _until(lambda: stop.requested == "pause")
            return "done"

        result = await _within(
            control.controlled(work(), store=opened_store, stop=stop, lease=lease, interval=0)
        )
    assert result == "done"
    assert agent.paused >= 1 and stop.primary is None
    assert [row.handled_at is not None for row in _requests(root)] == [True]


async def test_controlled_cancels_work_and_reraises_when_the_watcher_crashes(opened_store):
    started, cancelled = asyncio.Event(), []

    class Exploding(Wrapped):
        exploded = False

        def pending_controls(self, token: str) -> list[store.ControlRow]:
            if started.is_set() and not Exploding.exploded:
                Exploding.exploded = True
                raise RuntimeError("boom")
            return self._inner.pending_controls(token)

    with control.Lease(opened_store) as lease:
        with pytest.raises(RuntimeError, match="boom"):
            await _within(
                control.controlled(
                    _blocked_forever(started, cancelled),
                    store=Exploding(opened_store),
                    stop=StopSignal(),
                    lease=lease,
                    interval=0,
                )
            )
    assert cancelled == [True]


async def test_controlled_closes_the_window_then_sweeps_once_on_exit(root, opened_store):
    events: list[str] = []

    class Recording(Wrapped):
        def pending_controls(self, token: str) -> list[store.ControlRow]:
            events.append("pending")
            return self._inner.pending_controls(token)

        def close_window(self, token: str) -> None:
            events.append("close_window")
            self._inner.close_window(token)

    recording, stop = Recording(opened_store), StopSignal()
    with control.Lease(recording) as lease:

        async def work() -> str:
            # The watcher's first tick has run and it is now parked on a long
            # interval, so only the final sweep can see this request.
            await _until(lambda: "pending" in events)
            _send(root, lease.token, "pause")
            return "done"

        assert (
            await _within(
                control.controlled(work(), store=recording, stop=stop, lease=lease, interval=3600)
            )
            == "done"
        )
        assert events == ["pending", "close_window", "pending"]
        assert stop.requested == "pause"
        row = _read_lease(root)
        assert row is not None and row.accepting is False
    assert [row.handled_at is not None for row in _requests(root)] == [True]


async def test_controlled_cancels_work_on_exception_and_always_stops_the_watcher(
    root, opened_store
):
    before = asyncio.all_tasks()
    stop = StopSignal()
    with control.Lease(opened_store) as lease:
        # Review Focus 5: the task running `controlled` is cancelled from outside.
        started, cancelled = asyncio.Event(), []
        outer = asyncio.create_task(
            control.controlled(
                _blocked_forever(started, cancelled),
                store=opened_store,
                stop=stop,
                lease=lease,
                interval=0,
            )
        )
        await _within(started.wait())
        _send(root, lease.token, "cancel")
        outer.cancel()
        with pytest.raises(asyncio.CancelledError):
            await outer
        assert cancelled == [True]
        assert asyncio.all_tasks() == before
        row = _read_lease(root)
        assert row is not None and row.accepting is False
        assert stop.requested == "cancel"

        async def failing() -> str:
            raise ValueError("work failed")

        with pytest.raises(ValueError, match="work failed"):
            await _within(
                control.controlled(failing(), store=opened_store, stop=stop, lease=lease, interval=0)
            )
        assert asyncio.all_tasks() == before
