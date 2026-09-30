"""Process-wide locks on files beside the project database (spec X7, X8).

A `ProcessLock` layers an `fcntl.flock` on a file under the data directory
(`paths.project_lock_path`) over an in-process `threading.RLock`. The in-process
lock is always taken before the flock and released after it. The lock is
reentrant per thread: only the outermost `acquire` opens the file and takes the
flock, because a second flock on a new descriptor would block its own process.

A thread holds at most one `ProcessLock` at a time; asking for a second,
different one raises `LockOrderError`, which is what keeps the `board` and `git`
locks from ever being held together. A crashed holder releases its flock when
the kernel closes its descriptor, so this module has no liveness mechanism of
its own (no pid file, no heartbeat, no staleness rule). Nothing here opens a
store transaction.
"""

from __future__ import annotations

import fcntl
import os
import threading
import time
from pathlib import Path

from agent_manager import paths

LOCK_TIMEOUT_SECONDS = 600.0
"""Default wait for a `ProcessLock`, in seconds."""

_BACKOFF_FIRST = 0.05
_BACKOFF_CAP = 0.5
_BACKOFF_MAX_EXPONENT = 4
"""`0.05 * 2**4` already exceeds the cap; bounding the exponent keeps a long wait
from overflowing the float conversion of `2**n`."""


class LockTimeoutError(RuntimeError):
    """A `ProcessLock` was not obtained within its timeout."""

    def __init__(self, path: Path, timeout: float) -> None:
        super().__init__(f"timed out after {timeout}s waiting for the lock {path}")
        self.path = Path(path)
        self.timeout = timeout


class LockOrderError(RuntimeError):
    """A thread holding one `ProcessLock` asked for a different one."""


_HELD = threading.local()


def _held() -> set["ProcessLock"]:
    """The `ProcessLock`s the calling thread holds (at most one)."""
    held = getattr(_HELD, "locks", None)
    if held is None:
        held = set()
        _HELD.locks = held
    return held


def _backoff(attempt: int) -> float:
    """Seconds to wait before flock attempt `attempt + 1`: 0.05 doubling, capped at 0.5."""
    return min(_BACKOFF_FIRST * 2 ** min(attempt, _BACKOFF_MAX_EXPONENT), _BACKOFF_CAP)


def _flock(path: Path, timeout: float) -> int:
    """Open `path` and take an exclusive flock on it, polling until `timeout`.

    Returns the open, non-inheritable descriptor holding the flock. `timeout <= 0`
    tries once. Raises `LockTimeoutError` when the time runs out; the descriptor
    is closed on every failure.
    """
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    os.set_inheritable(fd, False)
    try:
        deadline = time.monotonic() + timeout
        attempt = 0
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return fd
            except BlockingIOError:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise LockTimeoutError(path, timeout) from None
                threading.Event().wait(min(_backoff(attempt), remaining))
                attempt += 1
    except BaseException:
        os.close(fd)
        raise


class ProcessLock:
    """A reentrant lock shared by the threads of this process and by other processes."""

    def __init__(
        self,
        path: Path,
        *,
        local: threading.RLock | None = None,
        timeout: float = LOCK_TIMEOUT_SECONDS,
    ) -> None:
        self.path = Path(path)
        self._local = local if local is not None else threading.RLock()
        self._timeout = timeout
        self._depth = 0  # touched only by the thread holding `_local`
        self._fd: int | None = None

    def __repr__(self) -> str:
        return f"ProcessLock({str(self.path)!r})"

    def acquire(self, timeout: float | None = None) -> None:
        held = _held()
        if held and self not in held:
            raise LockOrderError(
                f"{self.path}: this thread already holds "
                f"{sorted(str(lock.path) for lock in held)}"
            )
        wait = self._timeout if timeout is None else timeout
        start = time.monotonic()
        # `RLock.acquire(timeout=0)` raises, so a zero timeout is "try once".
        got = (
            self._local.acquire(timeout=wait)
            if wait > 0
            else self._local.acquire(blocking=False)
        )
        if not got:
            raise LockTimeoutError(self.path, wait)
        if self._depth == 0:
            # One budget for both layers: the flock gets what the in-process
            # wait left over, and a timeout reports the caller's `wait`.
            left = max(wait - (time.monotonic() - start), 0.0)
            try:
                self._fd = _flock(self.path, left)
            except LockTimeoutError:
                self._local.release()
                raise LockTimeoutError(self.path, wait) from None
            except BaseException:
                self._local.release()
                raise
            held.add(self)
        self._depth += 1

    def release(self) -> None:
        held = _held()
        if self not in held:
            raise RuntimeError(f"{self.path}: released by a thread that does not hold it")
        try:
            self._depth -= 1
            if self._depth == 0:
                fd, self._fd = self._fd, None
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                finally:
                    os.close(fd)
                    held.discard(self)
        finally:
            self._local.release()

    def __enter__(self) -> "ProcessLock":
        self.acquire()
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()


_LOCKS: dict[str, ProcessLock] = {}
"""One `ProcessLock` per lock file path, created on first use."""

_LOCKS_GUARD = threading.Lock()
"""Guards lookup-or-create in `_LOCKS`, so one lock file never gets two objects."""


def project_lock(
    root: Path, name: str, *, local: threading.RLock | None = None
) -> ProcessLock:
    """The process's one `ProcessLock` named `name` for the project at `root`.

    Keyed by `paths.project_lock_path`, which resolves `root`, so every spelling
    of one repository shares one object (and so re-enters instead of taking a
    second flock). `local=None` accepts whichever in-process lock the path was
    registered with; an explicit `local` other than the registered one raises
    `ValueError`.
    """
    path = paths.project_lock_path(root, name)
    key = str(path)
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = ProcessLock(path, local=local)
            _LOCKS[key] = lock
        elif local is not None and local is not lock._local:
            raise ValueError(
                f"{path}: already registered with a different in-process lock"
            )
        return lock
