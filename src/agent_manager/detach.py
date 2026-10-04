"""Hand a run's engine to a child in its own session (`am run --detach`, card aff9fdbf).

Process-level pieces only: the run's `run.log` and `report.json`, and
`fork_detacher`, which forks the child. What the child runs, and the lease
hand-off around it, live in `cli` (`hand_off_to_child`, `run_detached_child`).
This module imports only `paths` and the stdlib.

Fork, not a re-exec: the child runs stage 3 on the very `pre` and `recorded`
objects the parent built, so no run id is minted twice and `refresh_git` is
not run again. The caller makes sure no sqlite connection and no heartbeat
thread is alive when `fork_detacher` is called.
"""

from __future__ import annotations

import contextlib
import os
import sys
import tempfile
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

from agent_manager import paths

RUN_LOG_NAME = "run.log"
"""The detached child's stdout and stderr, under the run's directory."""

REPORT_NAME = "report.json"
"""The envelope a foreground run would have printed, written when the engine ends."""

FILE_MODE = 0o600
"""Both files are the operator's alone: they can quote prompts and paths."""

_GO = b"g"
"""The one byte the parent writes once the lease row names the child."""


@dataclass(frozen=True)
class Spawned:
    """A child that exists but has not started its body yet.

    `go` lets it run; `abort` closes the pipe without the go byte, and the
    child exits without touching any store. Exactly one of the two is called.
    """

    pid: int
    go: Callable[[], None]
    abort: Callable[[], None]


Detacher = Callable[[Callable[[], None], Path], Spawned]
"""Starts `body` in a child whose output goes to `log`; production's is `fork_detacher`."""


def create_run_log(run_id: str) -> Path:
    """`<data dir>/runs/<run_id>/run.log`, created if missing, mode exactly 0600.

    `fchmod` after the open, because `O_CREAT`'s mode is masked by the umask
    and an existing file keeps whatever mode it had.
    """
    path = paths.run_dir(run_id) / RUN_LOG_NAME
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, FILE_MODE)
    try:
        os.fchmod(fd, FILE_MODE)
    finally:
        os.close(fd)
    return path


def write_report(run_id: str, text: str) -> Path:
    """Write `text` and a newline to the run's `report.json`, atomically, mode 0600.

    A temp file in the same directory, fsynced, then `os.replace`d over the
    target, so a reader sees no file or a whole one, never a partial one.
    """
    directory = paths.run_dir(run_id)
    target = directory / REPORT_NAME
    fd, temp = tempfile.mkstemp(dir=directory, prefix=".report-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp, FILE_MODE)
        os.replace(temp, target)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temp)
        raise
    return target


def fork_detacher(body: Callable[[], None], log: Path) -> Spawned:
    """Fork a child in its own session that runs `body` once told to go.

    The child calls `setsid`, points stdin at /dev/null and stdout and stderr
    at `log`, then blocks reading the go-pipe. On the go byte it runs `body`;
    on EOF it runs nothing. Any exception is printed to `log`. It always
    ends with `os._exit`, so it never returns into Typer or pytest.
    """
    sys.stdout.flush()
    sys.stderr.flush()
    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:
        _child(body, log, read_fd, write_fd)
    os.close(read_fd)

    def go() -> None:
        try:
            os.write(write_fd, _GO)
        finally:
            os.close(write_fd)

    def abort() -> None:
        os.close(write_fd)

    return Spawned(pid=pid, go=go, abort=abort)


def _child(body: Callable[[], None], log: Path, read_fd: int, write_fd: int) -> NoReturn:
    code = 1
    try:
        os.close(write_fd)
        os.setsid()
        _redirect(log)
        signal = os.read(read_fd, 1)
        os.close(read_fd)
        if signal == _GO:
            body()
            code = 0
    except BaseException:
        traceback.print_exc()
    finally:
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        finally:
            os._exit(code)


def _redirect(log: Path) -> None:
    """stdin from /dev/null; stdout and stderr appended to `log`."""
    devnull = os.open(os.devnull, os.O_RDONLY)
    os.dup2(devnull, 0)
    os.close(devnull)
    out = os.open(log, os.O_WRONLY | os.O_APPEND)
    os.dup2(out, 1)
    os.dup2(out, 2)
    os.close(out)
