"""Soak tier: `am.db` writes against another OS process (card 1.3.6).

Single-store design D:378-385. A foreign process holding `BEGIN IMMEDIATE`
delays a `Store` write: one shorter than `busy_timeout` is waited out in one
attempt, one inside the retry budget is won by a retry, and one past the
budget raises `StoreBusyError` having written and spent nothing. A writer
`SIGKILL`ed between its event insert and its row write leaves no partial
write and consumes no `seq`. The holder is `storehelpers.hold_db`; order comes
from its pipe lines and from the attempt log below, never from sleeping.

The design's `busy_timeout` is 2 s per attempt inside a 10 s deadline
(D:196-197); `store_db.BUSY_TIMEOUT_SECONDS` is 30 s, which leaves no room for
a retry inside `RETRY_DEADLINE_SECONDS`. So the retry tests patch the
constants to the design's shape (timeout well under the deadline); the
production values are the parent story's to reconcile. `BUSY_TIMEOUT_SECONDS`
is read when a connection is made, so every patch of it precedes
`Store.open`.

`soak`, not unit: every test spawns a Python child; none runs `git`, `brd` or
`claude`, and none is production wiring.
"""

import os
import signal
import sqlite3
import subprocess
import sys
import textwrap
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
import storehelpers
from storehelpers import hold_db, reap, release_db

from agent_manager import models, paths
from agent_manager.store import db as store_db
from agent_manager.store import events as store_events
from agent_manager.store import journal as store_journal
from agent_manager.store import writer as store_writer

pytestmark = pytest.mark.soak

RUN_A = "run-2026-10-07-01"

NOW = datetime(2026, 10, 7, 9, 0, tzinfo=timezone.utc)

STORY = models.StoryRun(card_id="story-a", title="Story A", level=0, status="started")

WAIT = 30.0
"""Seconds any wait on a thread or child may take. It only bounds a broken
test; a healthy one never waits this long."""


def _run(repo: Path) -> models.Run:
    return models.Run(
        id=RUN_A,
        workflow="milestone",
        repo_dir=repo,
        base_branch="main",
        branch_prefix="m1/",
        status="started",
        started_at=NOW,
    )


def _events(conn: sqlite3.Connection) -> list[store_events.EventRow]:
    """`RUN_A`'s committed events, in `seq` order."""
    return store_events.read(conn, run_id=RUN_A)


def _journal_seqs() -> list[int]:
    """The `seq` of each line of `RUN_A`'s journal file, in `seq` order."""
    return [line.seq for line in store_journal.Journal(RUN_A).read()]


def test_the_foreign_holder_reports_head_and_releases_on_request(repo):
    st = store_writer.Store.open(repo, RUN_A)
    try:
        st.record_run(_run(repo))
        expected_head = store_events.head(st.read_connection)
    finally:
        st.close()
    probe = sqlite3.connect(paths.db_path(), isolation_level=None, timeout=0)
    holder, head = hold_db()
    try:
        with pytest.raises(sqlite3.OperationalError) as caught:
            probe.execute("BEGIN IMMEDIATE")
        release_db(holder)
        probe.execute("BEGIN IMMEDIATE")
        probe.execute("ROLLBACK")
    finally:
        reap(holder)
        probe.close()

    assert head == expected_head == 1
    assert store_db.is_busy(caught.value)
    assert holder.returncode == 0


def test_hold_db_fails_the_test_when_the_child_never_reports_held(repo, monkeypatch):
    # Review Focus 1: a holder that never holds fails loudly instead of
    # letting a test run uncontended; `hold_db` reaps it first.
    monkeypatch.setattr(storehelpers, "DB_HOLDER", "print('not held', flush=True)")

    with pytest.raises(pytest.fail.Exception, match="did not report 'held <head>'") as caught:
        hold_db()

    assert "'not held'" in str(caught.value)
