"""Cross-process holder and probe helpers for the lock tests (spec X7, X8).

Moved unchanged from `tests/test_locks.py` so the Wiring tests in
`tests/steps/test_rollup.py`, `tests/steps/test_worktree.py` and
`tests/test_orchestrate.py` can use the same child processes. Importable as
`lockhelpers` because `pyproject.toml` puts `tests/` on `pythonpath`
(`--import-mode=importlib` adds nothing to `sys.path` itself).

Children inherit the test's `XDG_DATA_HOME` (tests/conftest.py), so they flock
the same files as the test process. Order comes from pipes: `_holder` returns
only after the child printed "held", and `_release` only after it printed
"released".
"""

from __future__ import annotations

import contextlib
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

HOLDER = textwrap.dedent(
    """
    import sys
    from pathlib import Path
    from agent_manager import locks
    lock = locks.project_lock(Path(sys.argv[1]), sys.argv[2])
    lock.acquire()
    print("held", flush=True)
    sys.stdin.readline()          # released by the parent
    lock.release()
    print("released", flush=True)
    """
)

PROBE = textwrap.dedent(
    """
    import fcntl, os, sys
    from pathlib import Path
    from agent_manager import paths
    path = paths.project_lock_path(Path(sys.argv[1]), sys.argv[2])
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("busy", flush=True)
    else:
        print("free", flush=True)
    finally:
        os.close(fd)
    """
)


def _reap(child: subprocess.Popen[str]) -> None:
    """Kill `child` if it is still running, wait for it and close its pipes."""
    if child.poll() is None:
        child.kill()
    child.wait()
    for stream in (child.stdin, child.stdout):
        if stream is not None and not stream.closed:
            with contextlib.suppress(OSError):
                stream.close()


def _holder(root: Path, name: str) -> subprocess.Popen[str]:
    """A child process holding `project_lock(root, name)` until `_release`."""
    child = subprocess.Popen(
        [sys.executable, "-c", HOLDER, str(root), name],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    line = child.stdout.readline().strip()
    if line != "held":
        _reap(child)
        pytest.fail(f"holder child did not report 'held' (got {line!r})")
    return child


def _release(child: subprocess.Popen[str]) -> None:
    """Tell a `_holder` child to release, and wait until it has."""
    child.stdin.write("\n")
    child.stdin.flush()
    assert child.stdout.readline().strip() == "released"
    child.stdin.close()
    assert child.wait() == 0
    child.stdout.close()


def _probe(root: Path, name: str) -> str:
    """`"busy"` if another process holds the flock on the lock file, else `"free"`."""
    result = subprocess.run(
        [sys.executable, "-c", PROBE, str(root), name],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()
