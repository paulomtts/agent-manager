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
import os
import socket
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, TypeVar

import pytest

from agent_manager import control, store

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
