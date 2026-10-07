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


@dataclass
class AttemptLog:
    """Every call of every job `store_db.run_with_retry` was handed, per write.

    `calls` holds one `(operation, entries)` per `run_with_retry` call, in
    call order; `entries` gets `("start", n)` before the job's n-th call and
    `("busy", n)` when that call raised a busy `sqlite3.OperationalError`.
    Entries are appended on the store's writer thread; `wait_for` blocks the
    test thread until one is logged.
    """

    calls: list[tuple[str, list[tuple[str, int]]]] = field(default_factory=list)
    changed: threading.Condition = field(default_factory=threading.Condition)

    def of(self, operation: str) -> list[list[tuple[str, int]]]:
        """The entries of each call for `operation`, in call order."""
        with self.changed:
            return [list(entries) for name, entries in self.calls if name == operation]

    def wait_for(self, operation: str, entry: tuple[str, int]) -> None:
        """Return once `entry` is logged by a call for `operation`; fail after `WAIT`."""
        with self.changed:
            found = self.changed.wait_for(
                lambda: any(
                    entry in entries for name, entries in self.calls if name == operation
                ),
                timeout=WAIT,
            )
        if not found:
            pytest.fail(f"{operation} never logged {entry}: {self.calls}")

    def wrap(self, real: Callable[..., Any]) -> Callable[..., Any]:
        """`real` (`run_with_retry`), handed a job that logs each of its calls."""

        def run_with_retry(job: Callable[[], Any], *, operation: str, **kwargs: Any) -> Any:
            entries: list[tuple[str, int]] = []
            with self.changed:
                self.calls.append((operation, entries))

            def logged() -> Any:
                with self.changed:
                    n = sum(1 for kind, _ in entries if kind == "start") + 1
                    entries.append(("start", n))
                    self.changed.notify_all()
                try:
                    return job()
                except sqlite3.OperationalError as error:
                    if store_db.is_busy(error):
                        with self.changed:
                            entries.append(("busy", n))
                            self.changed.notify_all()
                    raise

            return real(logged, operation=operation, **kwargs)

        return run_with_retry


@pytest.fixture
def attempts(monkeypatch) -> AttemptLog:
    """The attempt log of every Store write this test makes.

    `writer.py` calls `store_db.run_with_retry` through the module at call
    time, so the patch reaches every job of every `Store`.
    """
    log = AttemptLog()
    monkeypatch.setattr(store_db, "run_with_retry", log.wrap(store_db.run_with_retry))
    return log


def _patch_budget(
    monkeypatch,
    *,
    busy_timeout: float,
    first_pause: float,
    pause_cap: float,
    attempts: int,
    deadline: float,
) -> None:
    """The retry constants, read at call time (`BUSY_TIMEOUT_SECONDS` at connect)."""
    monkeypatch.setattr(store_db, "BUSY_TIMEOUT_SECONDS", busy_timeout)
    monkeypatch.setattr(store_db, "RETRY_FIRST_PAUSE", first_pause)
    monkeypatch.setattr(store_db, "RETRY_PAUSE_CAP", pause_cap)
    monkeypatch.setattr(store_db, "RETRY_ATTEMPTS", attempts)
    monkeypatch.setattr(store_db, "RETRY_DEADLINE_SECONDS", deadline)


def test_a_foreign_hold_shorter_than_busy_timeout_is_waited_out_in_one_attempt(
    repo, attempts
):
    # BUSY_TIMEOUT_SECONDS stays at its default, far above the hold.
    st = store_writer.Store.open(repo, RUN_A)
    holder = None
    try:
        st.record_run(_run(repo))
        holder, head = hold_db()
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(st.record_story, STORY)
            attempts.wait_for("record_story", ("start", 1))
            release_db(holder)
            line = pending.result(timeout=WAIT)
        events = _events(st.read_connection)
    finally:
        if holder is not None:
            reap(holder)
        st.close()

    assert attempts.of("record_story") == [[("start", 1)]]
    assert [(event.kind, event.run_seq) for event in events] == [
        ("run_upsert", 1),
        ("story_upsert", 2),
    ]
    assert line.seq == 2
    assert events[-1].seq == head + 1


def test_a_foreign_hold_inside_the_retry_budget_succeeds_after_a_retry(
    repo, attempts, monkeypatch
):
    # A large attempt count and deadline keep "inside the budget" true on a
    # slow machine: what is pinned is that a retry happened and won.
    _patch_budget(
        monkeypatch,
        busy_timeout=0.2,
        first_pause=0.05,
        pause_cap=0.2,
        attempts=50,
        deadline=30.0,
    )
    st = store_writer.Store.open(repo, RUN_A)
    holder = None
    try:
        st.record_run(_run(repo))
        holder, head = hold_db()
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(st.record_story, STORY)
            attempts.wait_for("record_story", ("busy", 1))
            release_db(holder)
            line = pending.result(timeout=WAIT)
        events = _events(st.read_connection)
        loaded = st.load_run(RUN_A)
    finally:
        if holder is not None:
            reap(holder)
        st.close()

    (log,) = attempts.of("record_story")
    assert log[:2] == [("start", 1), ("busy", 1)]
    last_kind, k = log[-1]
    assert (last_kind, k >= 2) == ("start", True)
    assert ("busy", k) not in log
    # Review Focus 5: the re-run job left one event and mirrored one line.
    stories = [event for event in events if event.kind == "story_upsert"]
    assert [(event.run_seq, event.seq) for event in stories] == [(2, head + 1)]
    assert line.seq == 2
    assert loaded is not None
    assert [story.card_id for story in loaded.stories] == [STORY.card_id]
    assert _journal_seqs() == [1, 2]


def test_a_foreign_hold_past_the_retry_budget_raises_store_busy_error_and_spends_no_seq(
    repo, attempts, monkeypatch
):
    # The attempt cap, not the clock, ends the retry.
    _patch_budget(
        monkeypatch,
        busy_timeout=0.1,
        first_pause=0.01,
        pause_cap=0.05,
        attempts=3,
        deadline=30.0,
    )
    st = store_writer.Store.open(repo, RUN_A)
    holder = None
    try:
        st.record_run(_run(repo))
        holder, head = hold_db()
        with pytest.raises(store_db.StoreBusyError) as caught:
            st.record_story(STORY)
        # Review Focus 4: a WAL reader is not blocked by the holder; `open_db`
        # would be.
        reader = store_db.open_reader(paths.db_path())
        try:
            held_head = store_events.head(reader)
            held_kinds = [event.kind for event in _events(reader)]
        finally:
            reader.close()
        held_journal = _journal_seqs()
        release_db(holder)
        line = st.record_story(STORY)
        events = _events(st.read_connection)
    finally:
        if holder is not None:
            reap(holder)
        st.close()

    error = caught.value
    assert (error.operation, error.attempts) == ("record_story", 3)
    assert isinstance(error.__cause__, sqlite3.OperationalError)
    assert store_db.is_busy(error.__cause__)
    first, _ = attempts.of("record_story")
    assert first == [
        ("start", 1),
        ("busy", 1),
        ("start", 2),
        ("busy", 2),
        ("start", 3),
        ("busy", 3),
    ]
    assert held_head == head
    assert held_kinds == ["run_upsert"]
    assert held_journal == [1]
    assert line.seq == 2
    assert events[-1].seq == head + 1


CRASH_CHILD = textwrap.dedent(
    """
    import os, signal, sys
    from datetime import datetime, timezone
    from pathlib import Path
    from agent_manager import models
    from agent_manager.store import writer as store_writer

    repo, run_id = Path(sys.argv[1]), sys.argv[2]
    st = store_writer.Store.open(repo, run_id)
    st.record_run(models.Run(
        id=run_id, workflow="milestone", repo_dir=repo, base_branch="main",
        branch_prefix="m1/", status="started",
        started_at=datetime(2026, 10, 7, 9, 0, tzinfo=timezone.utc),
    ))

    def killed_mid_job(conn, run_id_, story):
        count = conn.execute(
            "SELECT COUNT(*) FROM events WHERE run_id = ? AND kind = 'story_upsert'",
            (run_id_,),
        ).fetchone()[0]
        print(f"inserted {count}", flush=True)
        os.kill(os.getpid(), signal.SIGKILL)

    st._write_story_row = killed_mid_job
    st.record_story(models.StoryRun(card_id="story-a", title="Story A", level=0, status="started"))
    print("survived", flush=True)
    """
)
"""A child that commits `record_run`, then is `SIGKILL`ed inside `record_story`'s
job, after `_insert_event` and before the row write (`writer.py` `_record`). It
counts the run's `story_upsert` events on the job's own connection, which sees
its uncommitted insert, and prints `inserted <count>` before the kill."""


def test_a_writer_killed_between_event_insert_and_row_write_leaves_no_partial_write(
    repo, monkeypatch
):
    # D:378-380. The raising-hook variant is test_writer.py's
    # test_a_failed_row_write_leaves_no_event_and_consumes_no_run_seq.
    child = subprocess.run(
        [sys.executable, "-c", CRASH_CHILD, str(repo), RUN_A],
        capture_output=True,
        text=True,
        timeout=WAIT,
    )

    assert child.stdout.split("\n") == ["inserted 1", ""], child.stderr
    assert child.returncode == -signal.SIGKILL
    conn = store_db.open_db(repo)
    try:
        events = _events(conn)
        stories = conn.execute(
            "SELECT COUNT(*) FROM stories WHERE run_id = ?", (RUN_A,)
        ).fetchone()[0]
        counter = conn.execute(
            "SELECT seq FROM sqlite_sequence WHERE name = 'events'"
        ).fetchone()[0]
        head = store_events.head(conn)
    finally:
        conn.close()
    assert [(event.kind, event.run_seq) for event in events] == [("run_upsert", 1)]
    assert stories == 0
    assert counter == head
    assert _journal_seqs() == [1]

    # One attempt with a short timeout: a lock the dead child still held
    # would raise StoreBusyError here instead of being waited out.
    monkeypatch.setattr(store_db, "BUSY_TIMEOUT_SECONDS", 0.5)
    monkeypatch.setattr(store_db, "RETRY_ATTEMPTS", 1)
    st = store_writer.Store.open(repo, RUN_A)
    try:
        replayed = st.replay_events(RUN_A)
        loaded = st.load_run(RUN_A)
        line = st.record_story(STORY)
        after = _events(st.read_connection)
    finally:
        st.close()
    assert replayed == loaded
    assert line.seq == 2
    assert after[-1].seq == head + 1
