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

import os
import socket
from collections.abc import Callable
from datetime import datetime, timezone

from agent_manager.store import LeaseRow

CONTROL_POLL_SECONDS = 1.0
"""How often `watch` looks for new requests."""

HEARTBEAT_SECONDS = 5.0
"""How often a held `Lease` moves its `heartbeat_at`."""

LEASE_STALE_SECONDS = 30.0
"""A lease whose heartbeat is older than this is dead (C2)."""


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
    lease: LeaseRow,
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
