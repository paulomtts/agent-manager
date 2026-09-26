"""The one door between pygents' event loop and blocking engine code (pygents-engine design G2).

An agent phase blocks for as long as `claude -p` runs, and a step blocks on
git, a test suite or the board, so both run through `asyncio.to_thread`. A
`to_thread` worker cannot be cancelled: cancelling the awaiting task abandons
the thread, it does not stop it. So `call_agent` stops what the thread started.
Each call keeps its own record of the processes it spawned, reached from inside
the worker thread through `current_spawn_hook()`; on `CancelledError` every one
of them is killed with `launcher.kill_tree` before the error propagates, and a
process the worker spawns after the cancel is killed the moment it appears.

The hook is per thread and per call, so cancelling one call never touches a
process another concurrent call spawned. Nothing here imports pygents:
`dispatch.py` imports this module, and rule 1 keeps pygents inside the runtime
modules that need it.
"""

from __future__ import annotations

import asyncio
import subprocess
import threading
from collections.abc import Callable, Mapping
from typing import Any

from agent_manager.harness import launcher

SpawnHook = Callable[[subprocess.Popen], None]

_local = threading.local()
_last_spawned: subprocess.Popen[bytes] | None = None


class _Call:
    """The processes one `call_agent` spawned, and whether it was cancelled."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._spawned: list[subprocess.Popen[bytes]] = []
        self._cancelled = False

    def on_spawn(self, process: subprocess.Popen[bytes]) -> None:
        global _last_spawned
        _last_spawned = process
        with self._lock:
            self._spawned.append(process)
            cancelled = self._cancelled
        if cancelled:
            launcher.kill_tree(process)

    def cancel(self) -> None:
        with self._lock:
            self._cancelled = True
            spawned = list(self._spawned)
        for process in spawned:
            launcher.kill_tree(process)


def current_spawn_hook() -> SpawnHook | None:
    """The `on_spawn` bound to the `call_agent` running on this thread, else `None`."""
    return getattr(_local, "hook", None)


def last_spawned_for_tests() -> subprocess.Popen[bytes] | None:
    """The most recently spawned process of any call. Test-only."""
    return _last_spawned


async def call_agent(
    runner: Callable[[Any, Any, Any], Any], phase: Any, context: Any, rendered: Any
) -> Any:
    """`runner(phase, context, rendered)` off the loop; kill its processes if cancelled."""
    call = _Call()

    def work() -> Any:
        _local.hook = call.on_spawn
        try:
            return runner(phase, context, rendered)
        finally:
            _local.hook = None

    try:
        return await asyncio.to_thread(work)
    except asyncio.CancelledError:
        call.cancel()
        raise


async def call_step(fn: Callable[..., Any], kwargs: Mapping[str, Any]) -> Any:
    """`fn(**kwargs)` off the loop. A step spawns nothing the bridge must track."""
    return await asyncio.to_thread(lambda: fn(**kwargs))
