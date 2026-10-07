"""Live control of a running milestone: the lease and the request watcher (live control C1-C4).

A running `am` process claims its run with a `Lease`: a row in `run_leases`
under a fresh token, kept fresh by a daemon heartbeat thread. `am pause` and
`am cancel` in another process insert `run_controls` rows addressed to that
token. `watch`, on the run's event loop, turns this lease's pending rows into
`StopSignal.request` calls, which park the run through the existing
`ON_PAUSE` + `Parked` path. No control ever cancels a running phase.

SQLite is the only channel: no socket, fifo or signal handler (C1). This
module imports only `store`, `runtime.stop` and the stdlib; never `cli`,
`orchestrate` or `grafo`.
"""

from __future__ import annotations

import asyncio
import os
import socket
import sqlite3
import threading
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime, timezone
from types import TracebackType
from typing import NoReturn, TypeVar, cast
from uuid import uuid4

from agent_manager.runtime.stop import Command, StopSignal
from agent_manager.store import leases as store_leases
from agent_manager.store.writer import Store

CONTROL_POLL_SECONDS = 1.0
"""How often `watch` looks for new requests."""

HEARTBEAT_SECONDS = 5.0
"""How often a held `Lease` moves its `heartbeat_at`."""

LEASE_STALE_SECONDS = 30.0
"""A lease whose heartbeat is older than this is dead (C2)."""

T = TypeVar("T")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def pid_alive(pid: int) -> bool:
    """Whether process `pid` exists on this host.

    A non-positive pid is never alive: `os.kill(0, 0)` would signal this
    process's own group and succeed. `PermissionError` means the process
    exists but belongs to someone else, so it counts as alive.
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def lease_is_live(
    lease: store_leases.LeaseRow,
    *,
    now: datetime,
    host: str = socket.gethostname(),
    alive: Callable[[int], bool] = pid_alive,
    stale_after: float = LEASE_STALE_SECONDS,
) -> bool:
    """C2: fresh heartbeat (boundary inclusive), and another host or a live pid here."""
    if (now - lease.heartbeat_at).total_seconds() > stale_after:
        return False
    return lease.host != host or alive(lease.pid)


def card_claim(card_id: str) -> str:
    """The `run_claims` key that says a run is driving card `card_id` (X5)."""
    return f"card:{card_id}"


def branch_claim(branch: str) -> str:
    """The `run_claims` key that says a run owns git branch `branch` (X5)."""
    return f"branch:{branch}"


class Lease:
    """This process's claim on a run and its keys, held for a `with` block (C2, X5).

    `__enter__` takes a fresh token and calls `Store.take_lease` with `claims`,
    `now = clock()` and `is_live` built on `lease_is_live` at that `now`, so a
    live holder of the run or of any key refuses the lease
    (`store_leases.LeaseHeldError`, `store_leases.ClaimHeldError`) and a dead one is taken
    over and kept in `displaced`. Only then does it start a daemon heartbeat
    thread. That thread waits on a `threading.Event`, never `time.sleep`, so
    `__exit__` wakes it at once. `__exit__` stops and joins it, then releases
    this token's claims, then this token's lease, on any exit, and never
    swallows the exception. A process that took the lease over keeps its rows.

    `am run --detach` (card aff9fdbf) adds two things. `hand_off()` stops and
    joins the heartbeat and unbinds the store but releases nothing; `__exit__`
    then does nothing, so the token and its claims outlive this process for a
    child to adopt. `adopt=token` enters around a token that already holds the
    run: no `take_lease`, `Store.adopt_lease` instead (which refuses a token
    that no longer holds it), one beat at once, then the heartbeat; its
    `__exit__` releases exactly as above.
    """

    def __init__(
        self,
        store: Store,
        *,
        claims: Sequence[str] = (),
        heartbeat: float = HEARTBEAT_SECONDS,
        clock: Callable[[], datetime] = _utcnow,
        pid: int | None = None,
        host: str | None = None,
        adopt: str | None = None,
    ) -> None:
        self._store = store
        self._claims = tuple(claims)
        self._heartbeat = heartbeat
        self._clock = clock
        self._pid = pid
        self._host = host
        self._adopt = adopt
        self._handed_off = False
        self._stopped = threading.Event()
        self._thread: threading.Thread | None = None
        self.token = ""
        self.displaced: store_leases.LeaseRow | None = None

    def __enter__(self) -> Lease:
        if self._adopt is None:
            self.token = uuid4().hex
            now = self._clock()
            taken = self._store.take_lease(
                token=self.token,
                pid=os.getpid() if self._pid is None else self._pid,
                host=socket.gethostname() if self._host is None else self._host,
                now=now,
                is_live=lambda row: lease_is_live(row, now=now),
                claims=self._claims,
            )
            self.displaced = taken.displaced
        else:
            self.token = self._adopt
            self._store.adopt_lease(self.token)
            self.displaced = None
            self.beat()
        self._handed_off = False
        self._stopped.clear()
        self._thread = threading.Thread(
            target=self._keep_beating, name="am-lease-heartbeat", daemon=True
        )
        self._thread.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._handed_off:
            # The token and its claims now belong to the detached child.
            return
        self._stop_heartbeat()
        try:
            try:
                self._store.release_claims(self.token)
            finally:
                self._store.release_lease(self.token)
        finally:
            # This process no longer holds the run: stop fencing its writes to
            # a token that is gone, as M9's store never fenced them.
            self._store.bind_lease(None)

    def hand_off(self) -> str:
        """Stop beating and unbind the store, releasing nothing; return the token.

        For `am run --detach`: called inside the `with` block, so the block's
        exit leaves the lease row and its claims for the child to adopt.
        """
        self._stop_heartbeat()
        self._store.bind_lease(None)
        self._handed_off = True
        return self.token

    def _stop_heartbeat(self) -> None:
        self._stopped.set()
        if self._thread is not None:
            self._thread.join()
            self._thread = None

    def beat(self) -> None:
        """Move this lease's heartbeat to `clock()`."""
        self._store.beat(self.token, self._clock())

    def close_window(self) -> None:
        """Stop accepting control requests under this lease."""
        self._store.close_window(self.token)

    def _keep_beating(self) -> None:
        while not self._stopped.wait(self._heartbeat):
            try:
                self.beat()
            except sqlite3.OperationalError:
                # A second process holds the database; the next beat retries.
                continue


def apply_pending(
    store: Store,
    stop: StopSignal,
    token: str,
    *,
    clock: Callable[[], datetime] = _utcnow,
) -> list[store_leases.ControlRow]:
    """Apply this lease's unhandled requests in `seq` order and mark each handled.

    Only rows addressed to `token` are read (C4), so a request sent to an
    earlier life of the run never reaches this one. Returns the rows applied.
    """
    applied: list[store_leases.ControlRow] = []
    for row in store.pending_controls(token):
        stop.request(cast(Command, row.command))
        store.mark_control_handled(row.seq, clock())
        applied.append(row)
    return applied


async def watch(
    store: Store,
    stop: StopSignal,
    token: str,
    *,
    interval: float = CONTROL_POLL_SECONDS,
    clock: Callable[[], datetime] = _utcnow,
) -> NoReturn:
    """Apply this lease's requests every `interval` seconds, forever.

    A `sqlite3.OperationalError` (a second process holding the database) is
    swallowed and retried on the next tick; any other error ends the watcher.
    """
    while True:
        try:
            apply_pending(store, stop, token, clock=clock)
        except sqlite3.OperationalError:
            pass
        await asyncio.sleep(interval)


async def controlled(
    work: Awaitable[T],
    *,
    store: Store,
    stop: StopSignal,
    lease: Lease,
    interval: float = CONTROL_POLL_SECONDS,
    clock: Callable[[], datetime] = _utcnow,
) -> T:
    """Run `work` with `watch` beside it and return `work`'s result.

    A control never cancels `work`; it only parks the run through `stop`.
    `work` is cancelled only when the watcher crashes (its error is re-raised)
    or on any other exception, including this task being cancelled. On every
    exit the watcher is stopped, then the window is closed, then one final
    sweep runs. In that order, no request can be accepted after the sweep.
    """
    work_task = asyncio.ensure_future(work)
    watcher = asyncio.create_task(
        watch(store, stop, lease.token, interval=interval, clock=clock)
    )
    try:
        done, _ = await asyncio.wait(
            {work_task, watcher}, return_when=asyncio.FIRST_COMPLETED
        )
        if work_task in done:
            return work_task.result()
        watcher.result()
        raise RuntimeError("the control watcher stopped without an error")
    except BaseException:
        work_task.cancel()
        await asyncio.gather(work_task, return_exceptions=True)
        raise
    finally:
        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)
        lease.close_window()
        apply_pending(store, stop, lease.token, clock=clock)
