"""A foreign process holding `am.db`'s write lock, for the store contention
tests (card 1.3.6).

The child is plain Python `sqlite3`, not `am`: to SQLite it is the same as the
`sqlite3` shell the design names as a foreign holder, and it needs no shell
installed. It inherits the test's `XDG_DATA_HOME`, so it opens the same
`am.db` as the test. Order comes from pipes, never from sleeps (the
`lockhelpers` pattern): `hold_db` returns only after the child printed
`held <head>`, and `release_db` only after it printed `released`. Importable
as `storehelpers` because `pyproject.toml` puts `tests/` on `pythonpath`.
"""

from __future__ import annotations

import contextlib
import subprocess
import sys
import textwrap

import pytest

DB_HOLDER = textwrap.dedent(
    """
    import sqlite3, sys
    from agent_manager import paths
    conn = sqlite3.connect(paths.db_path(), isolation_level=None, timeout=5.0)
    conn.execute("BEGIN IMMEDIATE")
    head = conn.execute("SELECT COALESCE(MAX(seq), 0) FROM events").fetchone()[0]
    print(f"held {head}", flush=True)
    sys.stdin.readline()          # released by the parent
    conn.execute("ROLLBACK")
    conn.close()
    print("released", flush=True)
    """
)
"""The holder child: `BEGIN IMMEDIATE` on `am.db`, report `held <head>` (the
largest `events.seq` its transaction sees), hold until a line on stdin, then
`ROLLBACK` and report `released`. Its 5 s `timeout` lets it wait out a write
already in flight when it starts."""


def reap(child: subprocess.Popen[str]) -> None:
    """Kill `child` if it is still running, wait for it and close its pipes."""
    if child.poll() is None:
        child.kill()
    child.wait()
    for stream in (child.stdin, child.stdout):
        if stream is not None and not stream.closed:
            with contextlib.suppress(OSError):
                stream.close()


def hold_db() -> tuple[subprocess.Popen[str], int]:
    """A child holding `am.db`'s write lock until `release_db`, and the `events`
    head it read inside its transaction. Fails the test if the child exits or
    prints anything else first."""
    child = subprocess.Popen(
        [sys.executable, "-c", DB_HOLDER],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    line = child.stdout.readline().strip()
    word, _, head = line.partition(" ")
    if word != "held" or not head.isdigit():
        reap(child)
        pytest.fail(f"db holder child did not report 'held <head>' (got {line!r})")
    return child, int(head)


def release_db(child: subprocess.Popen[str]) -> None:
    """Tell a `hold_db` child to roll back, and wait until it has exited 0."""
    child.stdin.write("release\n")
    child.stdin.flush()
    assert child.stdout.readline().strip() == "released"
    child.stdin.close()
    assert child.wait() == 0
    child.stdout.close()
