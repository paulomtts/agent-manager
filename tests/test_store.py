"""Behaviour of the SQLite projection and the append-only journal (spec §9, D5).

This module touches real files — a SQLite database and a JSONL journal — so it
is not in the "pure functions" tier of spec §14. These are the deterministic,
no-network, temporary-directory tests of the "steps" I/O tier: real temp DB
files and real temp journals rather than mocks. None of them dispatches a
harness, so none belongs in the single opt-in "end to end" test, and they all
run in the default `uv run pytest` suite.

`XDG_DATA_HOME` and `HOME` are redirected into pytest's tmp_path so `paths`
writes nowhere real.
"""

import ast
import dataclasses
import json
import sqlite3
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import get_args

import pytest
from pydantic import ValidationError

from agent_manager import models, paths, store

RUN_ID = "run-2026-09-23-01"


@pytest.fixture
def repo(monkeypatch, tmp_path) -> Path:
    """A redirected data dir plus a stand-in for the project worktree."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = tmp_path / "repo"
    project.mkdir()
    return project


def _dispatch(card: str = "ef248597", phase: str = "implement", n: int = 1) -> models.Dispatch:
    return models.Dispatch(
        harness="claude",
        model="sonnet",
        role="coder",
        cwd=Path(f"/repo/.claude/worktrees/m1/{card}"),
        prompt_path=Path(f"/runs/{RUN_ID}/{card}/{phase}.{n}/prompt.txt"),
        result_path=Path(f"/runs/{RUN_ID}/{card}/{phase}.{n}/result.json"),
    )


def _run(repo: Path, run_id: str = RUN_ID) -> models.Run:
    return models.Run(
        id=run_id,
        workflow="milestone",
        repo_dir=repo,
        base_branch="main",
        branch_prefix="m1/",
        status="started",
        started_at=datetime(2026, 9, 23, 10, 0, tzinfo=timezone.utc),
        config=models.RunConfig(
            max_concurrent_stories=2,
            harness_map={"coder": models.HarnessAssignment(harness="claude", model="sonnet")},
        ),
    )


def test_open_db_creates_the_project_file_in_wal_mode(repo):
    conn = store.open_db(repo)
    try:
        assert paths.project_db_path(repo).exists()
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        conn.close()


def test_open_db_connection_can_be_used_from_another_thread(repo):
    # P2: the threads of one process share one connection, so open_db must not
    # pin it to the thread that opened it. The busy timeout is explicit and
    # WAL mode is kept.
    conn = store.open_db(repo)
    try:
        counts: list[int] = []
        errors: list[BaseException] = []

        def query() -> None:
            try:
                counts.append(conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0])
            except BaseException as error:  # surfaced by the assertion below
                errors.append(error)

        worker = threading.Thread(target=query)
        worker.start()
        worker.join()

        assert errors == []
        assert counts == [0]
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == int(
            store.BUSY_TIMEOUT_SECONDS * 1000
        )
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        conn.close()


def _hold_fresh_db_reserved(repo: Path) -> sqlite3.Connection:
    """A second connection holding a RESERVED lock on a fresh, pre-WAL database.

    It must be `BEGIN IMMEDIATE`: SQLite fails `PRAGMA journal_mode=WAL` at once
    against a RESERVED lock without calling the busy handler, which is the race
    `open_db` retries. `BEGIN EXCLUSIVE` would make the busy handler run and so
    prove nothing.
    """
    path = paths.project_db_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    holder = sqlite3.connect(path, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    return holder


def test_open_db_waits_out_a_writer_holding_a_fresh_db_before_wal(repo):
    holder = _hold_fresh_db_reserved(repo)
    opened: list[sqlite3.Connection] = []
    errors: list[BaseException] = []

    def open_it() -> None:
        try:
            opened.append(store.open_db(repo))
        except BaseException as error:  # surfaced by the assertion below
            errors.append(error)

    worker = threading.Thread(target=open_it)
    try:
        worker.start()
        time.sleep(0.3)
        holder.execute("ROLLBACK")
    finally:
        holder.close()
    worker.join(timeout=10)

    assert not worker.is_alive()
    assert errors == []
    conn = opened[0]
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
    finally:
        conn.close()


def test_open_db_reraises_database_is_locked_after_the_deadline(repo, monkeypatch):
    monkeypatch.setattr(store, "BUSY_TIMEOUT_SECONDS", 0.3)
    holder = _hold_fresh_db_reserved(repo)
    try:
        started = time.monotonic()
        with pytest.raises(sqlite3.OperationalError, match="database is locked"):
            store.open_db(repo)
        elapsed = time.monotonic() - started
    finally:
        holder.execute("ROLLBACK")
        holder.close()

    assert elapsed >= 0.3
    assert elapsed < 5


def test_open_db_does_not_retry_an_error_other_than_database_is_locked(repo):
    # Junk bytes make the WAL pragma raise DatabaseError('file is not a
    # database') at once. With the default 30 s deadline, a retry would show up
    # as a long wait.
    path = paths.project_db_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"not a database" * 200)

    started = time.monotonic()
    with pytest.raises(sqlite3.DatabaseError, match="file is not a database"):
        store.open_db(repo)

    assert time.monotonic() - started < 2


def test_enable_wal_does_not_retry_an_operational_error_other_than_locked(
    tmp_path, monkeypatch
):
    # A read-only connection makes the WAL pragma raise
    # OperationalError('attempt to write a readonly database'): the same class as
    # the locked error, but a different message, so it must not be retried.
    path = tmp_path / "ro.db"
    writer = sqlite3.connect(path)
    writer.execute("CREATE TABLE t (x)")
    writer.commit()
    writer.close()
    pauses: list[float] = []
    monkeypatch.setattr(store.time, "sleep", pauses.append)
    monkeypatch.setattr(store, "BUSY_TIMEOUT_SECONDS", 2.0)

    reader = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        with pytest.raises(sqlite3.OperationalError, match="readonly database"):
            store._enable_wal(reader)
    finally:
        reader.close()

    assert pauses == []


def test_open_db_does_not_sleep_when_nothing_holds_a_lock(repo, monkeypatch):
    # Behavior 6: uncontended, the pragma runs once and open_db never pauses.
    pauses: list[float] = []
    monkeypatch.setattr(store.time, "sleep", pauses.append)

    conn = store.open_db(repo)
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        conn.close()

    assert pauses == []


def test_open_db_creates_every_projection_table(repo):
    conn = store.open_db(repo)
    try:
        names = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    finally:
        conn.close()
    assert {"runs", "stories", "subtasks", "phases", "attempts"} <= names


def test_reopening_an_existing_db_keeps_its_rows(repo):
    first = store.open_db(repo)
    first.execute(
        "INSERT INTO runs (id, workflow, repo_dir, base_branch, branch_prefix,"
        " status, started_at, config) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (RUN_ID, "milestone", str(repo), "main", "m1/", "started", None, "{}"),
    )
    first.commit()
    first.close()

    second = store.open_db(repo)
    try:
        rows = second.execute("SELECT id, workflow FROM runs").fetchall()
    finally:
        second.close()
    assert [(row["id"], row["workflow"]) for row in rows] == [(RUN_ID, "milestone")]


def test_append_writes_one_json_line_with_every_coordinate(repo):
    journal = store.Journal(RUN_ID)
    line = journal.append(
        "attempt_upsert",
        {"n": 1, "status": "started"},
        story="8831189b",
        card="ef248597",
        phase="implement",
        attempt=1,
    )

    assert journal.path == paths.run_dir(RUN_ID) / "journal.jsonl"
    text = journal.path.read_text(encoding="utf-8")
    assert text.endswith("\n")
    assert len(text.splitlines()) == 1

    record = json.loads(text)
    assert record["seq"] == 1
    assert record["run_id"] == RUN_ID
    assert record["event"] == "attempt_upsert"
    assert record["story"] == "8831189b"
    assert record["card"] == "ef248597"
    assert record["phase"] == "implement"
    assert record["attempt"] == 1
    assert record["payload"] == {"n": 1, "status": "started"}
    assert line.seq == 1


def test_run_level_lines_leave_the_lower_coordinates_null(repo):
    journal = store.Journal(RUN_ID)
    journal.append("run_upsert", {"status": "started"})

    record = json.loads(journal.path.read_text(encoding="utf-8"))
    assert record["story"] is None
    assert record["card"] is None
    assert record["phase"] is None
    assert record["attempt"] is None


def test_sequence_numbers_increase_by_one(repo):
    journal = store.Journal(RUN_ID)
    seqs = [journal.append("run_upsert", {"i": i}).seq for i in range(3)]
    assert seqs == [1, 2, 3]
    assert [line.seq for line in journal.read()] == [1, 2, 3]


def test_a_reopened_journal_continues_the_sequence(repo):
    first = store.Journal(RUN_ID)
    first.append("run_upsert", {"i": 0})
    first.append("run_upsert", {"i": 1})

    second = store.Journal(RUN_ID)
    assert second.last_seq() == 2
    assert second.append("run_upsert", {"i": 2}).seq == 3
    assert [line.payload["i"] for line in second.read()] == [0, 1, 2]


def test_appending_reads_nothing_from_disk_once_the_journal_is_open(repo, monkeypatch):
    # P2: one process writes a run, so the counter is read once, at open, and
    # appending never re-reads the file. Counted, not timed (P7).
    calls = {"read": 0, "last_seq": 0}
    real_read = store.Journal.read
    real_last_seq = store.Journal.last_seq

    def counting_read(self):
        calls["read"] += 1
        return real_read(self)

    def counting_last_seq(self):
        calls["last_seq"] += 1
        return real_last_seq(self)

    monkeypatch.setattr(store.Journal, "read", counting_read)
    monkeypatch.setattr(store.Journal, "last_seq", counting_last_seq)

    journal = store.Journal(RUN_ID)
    calls["read"] = 0
    calls["last_seq"] = 0

    seqs = [journal.append("run_upsert", {"i": i}).seq for i in range(50)]

    assert calls == {"read": 0, "last_seq": 0}
    assert seqs == list(range(1, 51))


def test_a_fresh_journal_on_an_existing_run_continues_after_the_last_line(repo):
    first = store.Journal(RUN_ID)
    for i in range(3):
        first.append("run_upsert", {"i": i})

    resumed = store.Journal(RUN_ID)
    assert resumed.append("run_upsert", {"i": 3}).seq == 4
    assert resumed.append("run_upsert", {"i": 4}).seq == 5
    assert [line.seq for line in resumed.read()] == [1, 2, 3, 4, 5]


def test_a_resumed_store_continues_the_journal_sequence(repo):
    # Review Focus 5: `Store.open` builds the journal, so a resumed run
    # numbers its next record after the last line already on disk.
    st = store.Store.open(repo, RUN_ID)
    try:
        st.record_run(_run(repo))
    finally:
        st.close()

    reopened = store.Store.open(repo, RUN_ID)
    try:
        line = reopened.record_run(_run(repo).model_copy(update={"status": "done"}))
    finally:
        reopened.close()

    assert line.seq == 2
    assert [line.seq for line in store.Journal(RUN_ID).read()] == [1, 2]


def test_a_journal_of_only_blank_lines_opens_at_zero(repo):
    # Review Focus 1: a crash between write and flush can leave blank lines
    # and nothing else. That is an empty journal, not a corrupt one.
    first = store.Journal(RUN_ID)
    first.path.write_text("\n\n", encoding="utf-8")

    journal = store.Journal(RUN_ID)
    assert journal.append("run_upsert", {"i": 0}).seq == 1


def test_a_journal_opened_on_out_of_order_lines_continues_after_the_highest(repo):
    # Review Focus 2: the counter is the highest seq on disk, not the seq of
    # the last line in file order.
    first = store.Journal(RUN_ID)
    first.append("run_upsert", {"i": 1})
    for seq in (3, 2):
        _append_raw(
            first,
            {
                "seq": seq,
                "ts": "2026-09-23T10:00:00+00:00",
                "run_id": RUN_ID,
                "event": "run_upsert",
                "payload": {"i": seq},
            },
        )

    journal = store.Journal(RUN_ID)
    assert journal.append("run_upsert", {"i": 4}).seq == 4
    assert [line.seq for line in journal.read()] == [1, 2, 3, 4]


def test_opening_a_corrupt_journal_raises_at_open(repo):
    # The counter is read at open, so a corrupt journal is reported there
    # rather than at the first append.
    first = store.Journal(RUN_ID)
    first.append("run_upsert", {"i": 0})
    with first.path.open("a", encoding="utf-8") as handle:
        handle.write("this is not json\n")

    with pytest.raises(store.CorruptJournalError) as excinfo:
        store.Journal(RUN_ID)
    message = str(excinfo.value)
    assert str(first.path) in message
    assert ":2:" in message

def test_reading_a_journal_that_does_not_exist_raises(repo):
    journal = store.Journal("run-never-started")
    with pytest.raises(store.MissingJournalError) as excinfo:
        journal.read()
    assert "run-never-started" in str(excinfo.value)
    assert str(journal.path) in str(excinfo.value)


def test_append_holds_the_lock_across_the_write_and_the_fsync(repo, monkeypatch):
    # Deterministic (P7): rather than racing threads and hoping to catch an
    # overlap, check the lock is held at the moment the line is fsynced.
    journal = store.Journal(RUN_ID)
    held_during_fsync: list[bool] = []
    real_fsync = store.os.fsync

    def spying_fsync(fd):
        held_during_fsync.append(journal._lock.locked())
        real_fsync(fd)

    monkeypatch.setattr(store.os, "fsync", spying_fsync)

    journal.append("run_upsert", {"i": 0})
    journal.append("run_upsert", {"i": 1})

    assert held_during_fsync == [True, True]
    assert journal._lock.locked() is False


def test_eight_threads_sharing_one_journal_write_800_whole_lines_numbered_1_to_800(repo):
    workers, per_worker = 8, 100
    journal = store.Journal(RUN_ID)
    start = threading.Barrier(workers)
    errors: list[BaseException] = []

    def work(worker: int) -> None:
        try:
            start.wait()
            for i in range(per_worker):
                journal.append("run_upsert", {"w": worker, "i": i})
        except BaseException as error:  # surfaced by the assertion below
            errors.append(error)

    threads = [threading.Thread(target=work, args=(w,)) for w in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    raw = [
        text
        for text in journal.path.read_text(encoding="utf-8").splitlines()
        if text.strip()
    ]
    assert len(raw) == workers * per_worker
    records = [json.loads(text) for text in raw]  # a torn line fails here
    assert sorted(record["seq"] for record in records) == list(
        range(1, workers * per_worker + 1)
    )
    for worker in range(workers):
        own = [record for record in records if record["payload"]["w"] == worker]
        assert [record["payload"]["i"] for record in sorted(own, key=lambda r: r["seq"])] == list(
            range(per_worker)
        )


def test_a_failed_write_releases_the_lock_and_does_not_spend_a_number(repo, tmp_path):
    # Review Focus 3: the path cannot be opened for appending, so nothing
    # lands on disk. The lock is released and the next append reuses the number.
    journal = store.Journal(RUN_ID)
    journal.append("run_upsert", {"i": 0})

    real_path = journal.path
    directory = tmp_path / "a-directory-not-a-file"
    directory.mkdir()
    journal.path = directory
    with pytest.raises(OSError):
        journal.append("run_upsert", {"i": "lost"})
    assert journal._lock.locked() is False

    journal.path = real_path
    assert journal.append("run_upsert", {"i": 1}).seq == 2
    assert [line.seq for line in journal.read()] == [1, 2]


def test_a_failed_fsync_after_the_write_spends_the_number_so_no_seq_repeats(repo, monkeypatch):
    # Once write and flush succeed the line is in the file, fsynced or not, so
    # retrying its number would put a duplicate seq on disk.
    journal = store.Journal(RUN_ID)
    journal.append("run_upsert", {"i": 0})

    real_fsync = store.os.fsync

    def failing_fsync(fd):
        raise OSError("fsync failed")

    monkeypatch.setattr(store.os, "fsync", failing_fsync)
    with pytest.raises(OSError, match="fsync failed"):
        journal.append("run_upsert", {"i": "unsynced"})
    assert journal._lock.locked() is False

    monkeypatch.setattr(store.os, "fsync", real_fsync)
    assert journal.append("run_upsert", {"i": 2}).seq == 3
    assert [line.seq for line in journal.read()] == [1, 2, 3]


def test_an_invalid_line_releases_the_lock_and_does_not_spend_a_number(repo):
    # Review Focus 4: JournalLine validation runs inside the lock.
    journal = store.Journal(RUN_ID)
    journal.append("run_upsert", {"i": 0})

    with pytest.raises(ValidationError):
        journal.append("not_an_event", {})  # type: ignore[arg-type]
    assert journal._lock.locked() is False

    assert journal.append("run_upsert", {"i": 1}).seq == 2
    assert [line.seq for line in journal.read()] == [1, 2]


def test_a_non_json_line_names_the_file_and_the_line_number(repo):
    journal = store.Journal(RUN_ID)
    journal.append("run_upsert", {"i": 0})
    with journal.path.open("a", encoding="utf-8") as handle:
        handle.write("this is not json\n")

    with pytest.raises(store.CorruptJournalError) as excinfo:
        journal.read()
    message = str(excinfo.value)
    assert str(journal.path) in message
    assert ":2:" in message


def test_a_truncated_final_line_is_an_error_but_a_blank_one_is_not(repo):
    # Review Focus 2: a crash mid-append leaves either nothing, a blank line, or
    # half a line. The blank one is noise; the half line is data loss and says so.
    journal = store.Journal(RUN_ID)
    journal.append("run_upsert", {"i": 0})
    with journal.path.open("a", encoding="utf-8") as handle:
        handle.write("\n")
    assert [line.payload["i"] for line in journal.read()] == [0]

    with journal.path.open("a", encoding="utf-8") as handle:
        handle.write('{"seq": 2, "run_id": "run-2026\n')
    with pytest.raises(store.CorruptJournalError) as excinfo:
        journal.read()
    assert ":3:" in str(excinfo.value)


def test_an_envelope_with_an_unknown_key_is_rejected(repo):
    journal = store.Journal(RUN_ID)
    journal.append("run_upsert", {"i": 0})
    with journal.path.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "seq": 2,
                    "ts": "2026-09-23T10:00:00+00:00",
                    "run_id": RUN_ID,
                    "event": "run_upsert",
                    "payload": {},
                    "operator": "someone",
                }
            )
            + "\n"
        )

    with pytest.raises(ValidationError) as excinfo:
        journal.read()
    assert "operator" in str(excinfo.value)


def test_a_run_directory_that_cannot_be_created_propagates_the_os_error(repo, tmp_path):
    # The journal defers directory creation to `paths.run_dir`, so the OS error
    # comes through untouched -- the store adds no fallback of its own.
    runs = tmp_path / "data" / "agent-manager" / "runs"
    runs.mkdir(parents=True)
    (runs / "run-blocked").write_text("not a directory")

    with pytest.raises(OSError):
        store.Journal("run-blocked")


def _story() -> models.StoryRun:
    return models.StoryRun(
        card_id="8831189b",
        title="Foundations: paths, run store and journal",
        level=0,
        status="started",
        tip_branch="m1/task-add-the-run-state-models-1535b285",
    )


def _subtask(card_id: str = "ef248597", base: str = "main") -> models.SubtaskRun:
    return models.SubtaskRun(
        card_id=card_id,
        branch=f"m1/task-{card_id}",
        base_branch=base,
        status="started",
        worktree_path=Path(f"/repo/.claude/worktrees/m1/task-{card_id}"),
    )


def test_record_run_writes_the_journal_line_and_the_row(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        line = st.record_run(_run(repo))
        assert line.event == "run_upsert"
        assert line.card is None

        row = st.connection.execute("SELECT * FROM runs WHERE id = ?", (RUN_ID,)).fetchone()
        assert row["workflow"] == "milestone"
        assert row["status"] == "started"
        assert row["repo_dir"] == str(repo)
        assert json.loads(row["config"])["max_concurrent_stories"] == 2
    finally:
        st.close()

    lines = store.Journal(RUN_ID).read()
    assert [line.event for line in lines] == ["run_upsert"]
    assert lines[0].payload["workflow"] == "milestone"
    assert "stories" not in lines[0].payload


def test_record_story_subtask_phase_and_attempt_write_their_rows(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        st.record_run(_run(repo))
        st.record_story(_story())
        st.record_subtask("8831189b", _subtask())
        st.record_phase(
            "8831189b",
            "ef248597",
            models.PhaseRun(
                name="implement",
                kind="agent",
                status="started",
                started_at=datetime(2026, 9, 23, 10, 13, tzinfo=timezone.utc),
            ),
        )
        attempt_line = st.record_attempt(
            "8831189b",
            "ef248597",
            "implement",
            models.Attempt(n=1, dispatch=_dispatch(), status="ok", exit_code=0, duration=4.5),
        )

        assert attempt_line.story == "8831189b"
        assert attempt_line.card == "ef248597"
        assert attempt_line.phase == "implement"
        assert attempt_line.attempt == 1

        assert st.connection.execute("SELECT COUNT(*) FROM stories").fetchone()[0] == 1
        assert st.connection.execute("SELECT COUNT(*) FROM subtasks").fetchone()[0] == 1
        phase_row = st.connection.execute("SELECT * FROM phases").fetchone()
        assert phase_row["kind"] == "agent"
        assert phase_row["ended_at"] is None
        attempt_row = st.connection.execute("SELECT * FROM attempts").fetchone()
        assert attempt_row["status"] == "ok"
        assert attempt_row["exit_code"] == 0
        assert attempt_row["duration"] == pytest.approx(4.5)
        assert json.loads(attempt_row["dispatch"])["role"] == "coder"
    finally:
        st.close()

    assert [line.event for line in store.Journal(RUN_ID).read()] == [
        "run_upsert",
        "story_upsert",
        "subtask_upsert",
        "phase_upsert",
        "attempt_upsert",
    ]


def test_a_failed_sqlite_write_still_leaves_the_journal_line(repo):
    # §9 line 365: the journal is appended first. Closing the connection is a
    # real SQLite failure -- no mock -- and the line must survive it.
    st = store.Store.open(repo, RUN_ID)
    st.record_run(_run(repo))
    st.close()

    with pytest.raises(sqlite3.Error):
        st.record_story(_story())

    lines = store.Journal(RUN_ID).read()
    assert [line.event for line in lines] == ["run_upsert", "story_upsert"]
    assert lines[1].story == "8831189b"

    reopened = store.Store.open(repo, RUN_ID)
    try:
        assert reopened.connection.execute("SELECT COUNT(*) FROM stories").fetchone()[0] == 0
    finally:
        reopened.close()


def test_recording_a_node_again_updates_it_without_duplicating_or_reordering(repo):
    # Review Focus 5: pending -> started -> done is the same node three times.
    st = store.Store.open(repo, RUN_ID)
    try:
        st.record_run(_run(repo))
        st.record_story(_story())
        first = _subtask("fdebc746")
        second = _subtask("1535b285", base="m1/task-fdebc746")
        st.record_subtask("8831189b", first)
        st.record_subtask("8831189b", second)

        st.record_subtask("8831189b", first.model_copy(update={"status": "done"}))
        st.record_subtask("8831189b", second.model_copy(update={"status": "failed"}))

        rows = st.connection.execute(
            "SELECT card_id, status FROM subtasks WHERE run_id = ? AND story_id = ?"
            " ORDER BY position",
            (RUN_ID, "8831189b"),
        ).fetchall()
    finally:
        st.close()

    assert [(row["card_id"], row["status"]) for row in rows] == [
        ("fdebc746", "done"),
        ("1535b285", "failed"),
    ]


def test_run_status_transitions_replace_the_single_run_row(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        run = _run(repo)
        st.record_run(run)
        st.record_run(run.model_copy(update={"status": "done"}))
        rows = st.connection.execute("SELECT id, status FROM runs").fetchall()
    finally:
        st.close()

    assert [(row["id"], row["status"]) for row in rows] == [(RUN_ID, "done")]
    assert len(store.Journal(RUN_ID).read()) == 2


def test_recording_a_run_writes_nothing_into_the_repo_directory(repo, tmp_path):
    # D5 and §4: the two stores are independent of the board and of the
    # worktree. Nothing agent-manager writes may land in the repo.
    st = store.Store.open(repo, RUN_ID)
    try:
        st.record_run(_run(repo))
        st.record_story(_story())
        st.record_subtask("8831189b", _subtask())
        st.record_phase("8831189b", "ef248597", models.PhaseRun(name="implement", kind="agent"))
        st.record_attempt(
            "8831189b", "ef248597", "implement", models.Attempt(n=1, dispatch=_dispatch())
        )
    finally:
        st.close()

    assert list(repo.iterdir()) == []
    assert paths.project_db_path(repo).is_relative_to(tmp_path / "data")
    assert store.Journal(RUN_ID).path.is_relative_to(tmp_path / "data")


def _record_full_run(st: store.Store, repo: Path) -> None:
    """One run, two subtasks, three phases, a finished and an in-flight attempt."""
    st.record_run(_run(repo))
    st.record_story(_story())

    st.record_subtask("8831189b", _subtask("fdebc746").model_copy(update={"status": "done"}))
    st.record_phase(
        "8831189b",
        "fdebc746",
        models.PhaseRun(
            name="verify",
            kind="deterministic",
            status="done",
            started_at=datetime(2026, 9, 23, 10, 5, tzinfo=timezone.utc),
            ended_at=datetime(2026, 9, 23, 10, 6, tzinfo=timezone.utc),
        ),
    )

    st.record_subtask("8831189b", _subtask("ef248597", base="m1/task-fdebc746"))
    st.record_phase(
        "8831189b",
        "ef248597",
        models.PhaseRun(
            name="explore",
            kind="agent",
            status="done",
            started_at=datetime(2026, 9, 23, 10, 10, tzinfo=timezone.utc),
            ended_at=datetime(2026, 9, 23, 10, 12, tzinfo=timezone.utc),
        ),
    )
    st.record_attempt(
        "8831189b",
        "ef248597",
        "explore",
        models.Attempt(
            n=1,
            dispatch=_dispatch(card="ef248597", phase="explore"),
            status="ok",
            exit_code=0,
            duration=31.25,
            prompt_path=Path(f"/runs/{RUN_ID}/ef248597/explore.1/prompt.txt"),
            result_path=Path(f"/runs/{RUN_ID}/ef248597/explore.1/result.json"),
            stdout_path=Path(f"/runs/{RUN_ID}/ef248597/explore.1/stdout.log"),
        ),
    )
    st.record_phase(
        "8831189b",
        "ef248597",
        models.PhaseRun(
            name="implement",
            kind="agent",
            status="started",
            started_at=datetime(2026, 9, 23, 10, 13, tzinfo=timezone.utc),
        ),
    )
    st.record_attempt(
        "8831189b",
        "ef248597",
        "implement",
        models.Attempt(n=1, dispatch=_dispatch(card="ef248597", phase="implement")),
    )


def test_load_run_rebuilds_the_tree_in_recorded_order(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        _record_full_run(st, repo)
        loaded = st.load_run(RUN_ID)
    finally:
        st.close()

    assert loaded is not None
    assert loaded.id == RUN_ID
    assert loaded.repo_dir == repo
    assert loaded.started_at == datetime(2026, 9, 23, 10, 0, tzinfo=timezone.utc)
    assert loaded.config.harness_map["coder"].model == "sonnet"

    story = loaded.stories[0]
    assert [subtask.card_id for subtask in story.subtasks] == ["fdebc746", "ef248597"]
    subtask = story.subtasks[1]
    assert subtask.base_branch == "m1/task-fdebc746"
    assert [phase.name for phase in subtask.phases] == ["explore", "implement"]

    finished = subtask.phases[0].attempts[0]
    assert finished.status == "ok"
    assert finished.duration == pytest.approx(31.25)
    assert finished.stdout_path == Path(f"/runs/{RUN_ID}/ef248597/explore.1/stdout.log")
    assert finished.dispatch.role == "coder"

    in_flight = subtask.phases[1].attempts[0]
    assert in_flight.status == "started"
    assert in_flight.exit_code is None
    assert in_flight.duration is None


def test_load_run_returns_none_for_an_unknown_run(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        assert st.load_run("run-never-started") is None
    finally:
        st.close()


def _truncate_db(repo: Path) -> Path:
    """Wipe the projection the way a crashed or corrupted disk would.

    The WAL sidecars are removed too: zeroing the main file while a populated
    `-wal` survives would not actually lose the rows.
    """
    db_path = paths.project_db_path(repo)
    db_path.write_bytes(b"")
    for suffix in ("-wal", "-shm"):
        db_path.with_name(db_path.name + suffix).unlink(missing_ok=True)
    return db_path


def test_a_db_truncated_mid_run_is_rebuilt_from_its_journal(repo):
    st = store.Store.open(repo, RUN_ID)
    _record_full_run(st, repo)
    before = st.load_run(RUN_ID)
    st.close()

    db_path = _truncate_db(repo)
    assert db_path.stat().st_size == 0

    rebuilt = store.Store.open(repo, RUN_ID)
    try:
        assert rebuilt.load_run(RUN_ID) is None
        returned = rebuilt.rebuild_from_journal(RUN_ID)
        after = rebuilt.load_run(RUN_ID)
    finally:
        rebuilt.close()

    assert after == before
    assert returned == before


def test_rebuilding_twice_changes_nothing_and_duplicates_nothing(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        _record_full_run(st, repo)
        once = st.rebuild_from_journal(RUN_ID)
        first = st.load_run(RUN_ID)
        twice = st.rebuild_from_journal(RUN_ID)
        second = st.load_run(RUN_ID)
        counts = {
            table: st.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("runs", "stories", "subtasks", "phases", "attempts")
        }
    finally:
        st.close()

    assert once == twice
    assert first == second
    assert counts == {"runs": 1, "stories": 1, "subtasks": 2, "phases": 3, "attempts": 2}


def test_an_in_flight_attempt_survives_the_rebuild_as_started(repo):
    # §9 lines 370-373: the store preserves `started` with nothing terminal.
    # Discarding it is the engine's job, not the store's.
    st = store.Store.open(repo, RUN_ID)
    _record_full_run(st, repo)
    st.close()

    _truncate_db(repo)
    rebuilt = store.Store.open(repo, RUN_ID)
    try:
        run = rebuilt.rebuild_from_journal(RUN_ID)
        row = rebuilt.connection.execute(
            "SELECT * FROM attempts WHERE phase = 'implement'"
        ).fetchone()
    finally:
        rebuilt.close()

    attempt = run.stories[0].subtasks[1].phases[1].attempts[0]
    assert attempt.status == "started"
    assert attempt.exit_code is None
    assert attempt.duration is None
    assert row["status"] == "started"
    assert row["exit_code"] is None

    terminal = run.stories[0].subtasks[1].phases[0].attempts[0]
    assert terminal.status == "ok"
    assert terminal.exit_code == 0


def test_rebuild_picks_up_a_journal_line_whose_row_never_landed(repo):
    # The other half of the ordering guarantee: the row the failed SQLite write
    # never produced is materialised by the rebuild.
    st = store.Store.open(repo, RUN_ID)
    st.record_run(_run(repo))
    st.close()
    with pytest.raises(sqlite3.Error):
        st.record_story(_story())

    reopened = store.Store.open(repo, RUN_ID)
    try:
        run = reopened.rebuild_from_journal(RUN_ID)
        row = reopened.connection.execute("SELECT * FROM stories").fetchone()
    finally:
        reopened.close()

    assert [story.card_id for story in run.stories] == ["8831189b"]
    assert row["card_id"] == "8831189b"
    assert row["status"] == "started"


def _append_raw(journal: store.Journal, record: dict) -> None:
    with journal.path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")


def test_a_line_with_an_unknown_payload_key_raises_out_of_rebuild(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        _record_full_run(st, repo)
        journal = st.journal
        _append_raw(
            journal,
            {
                "seq": journal.last_seq() + 1,
                "ts": "2026-09-23T10:20:00+00:00",
                "run_id": RUN_ID,
                "event": "attempt_upsert",
                "story": "8831189b",
                "card": "ef248597",
                "phase": "implement",
                "attempt": 1,
                "payload": {
                    "n": 1,
                    "dispatch": _dispatch().model_dump(mode="json"),
                    "tokens": 10,
                },
            },
        )
        with pytest.raises(ValidationError) as excinfo:
            st.rebuild_from_journal(RUN_ID)
    finally:
        st.close()
    assert "tokens" in str(excinfo.value)


# -- retired attempt usage keys (remove-cost-tracking §4.3) -------------------
#
# Every journal written before 2026-10-03 carries `tokens_in`, `tokens_out`
# and `cost` on each attempt line. `Attempt` no longer declares them, so
# `replay` sheds exactly those three names from an attempt payload and stays
# strict about everything else. Unit tier: real temp DB and journal.

_RETIRED_NULL = {"tokens_in": None, "tokens_out": None, "cost": None}
_RETIRED_SET = {"tokens_in": 8000, "tokens_out": 1500, "cost": 0.31}


def _append_old_attempt(journal: store.Journal, extra: dict) -> None:
    """Re-record implement attempt 1 as `ok`, the way an `am` from before
    2026-10-03 wrote it: today's payload plus `extra`."""
    payload = models.Attempt(
        n=1,
        dispatch=_dispatch(card="ef248597", phase="implement"),
        status="ok",
        exit_code=0,
        duration=12.5,
    ).model_dump(mode="json")
    _append_raw(
        journal,
        {
            "seq": journal.last_seq() + 1,
            "ts": "2026-09-23T10:20:00+00:00",
            "run_id": RUN_ID,
            "event": "attempt_upsert",
            "story": "8831189b",
            "card": "ef248597",
            "phase": "implement",
            "attempt": 1,
            "payload": {**payload, **extra},
        },
    )


@pytest.mark.parametrize("retired", [_RETIRED_NULL, _RETIRED_SET], ids=["null", "non_null"])
def test_an_attempt_line_carrying_the_retired_usage_keys_still_replays(repo, retired):
    st = store.Store.open(repo, RUN_ID)
    try:
        _record_full_run(st, repo)
        _append_old_attempt(st.journal, retired)
        replayed = st.replay_journal(RUN_ID)
        # The projection already holds this run, so the rebuild runs its
        # `diverging` pre-check over the same old lines first.
        rebuilt = st.rebuild_from_journal(RUN_ID)
        loaded = st.load_run(RUN_ID)
        assert loaded is not None
        mismatches = store.diverging(st.journal.read(), loaded)
    finally:
        st.close()

    assert replayed == rebuilt == loaded
    attempt = rebuilt.stories[0].subtasks[1].phases[1].attempts[0]
    assert (attempt.n, attempt.status, attempt.exit_code, attempt.duration) == (
        1,
        "ok",
        0,
        12.5,
    )
    for key in retired:
        assert not hasattr(attempt, key)
    assert set(retired).isdisjoint(attempt.model_dump())
    assert mismatches == []


@pytest.mark.parametrize("read", ["replay_journal", "rebuild_from_journal"])
def test_an_attempt_line_with_any_other_unknown_key_still_raises_naming_only_it(
    repo, read
):
    st = store.Store.open(repo, RUN_ID)
    try:
        _record_full_run(st, repo)
        _append_old_attempt(st.journal, {**_RETIRED_NULL, "operator": "x"})
        with pytest.raises(ValidationError) as excinfo:
            getattr(st, read)(RUN_ID)
    finally:
        st.close()

    assert [error["loc"] for error in excinfo.value.errors()] == [("operator",)]
    assert "operator" in str(excinfo.value)


@pytest.mark.parametrize(
    "event", ["run_upsert", "story_upsert", "subtask_upsert", "phase_upsert"]
)
def test_only_attempt_lines_shed_the_retired_usage_keys(repo, event):
    # No other node ever carried these keys, so on any other line they are
    # still an unknown key and still fail loudly.
    coordinates, payload = {
        "run_upsert": ({}, _run(repo).model_dump(mode="json", exclude={"stories"})),
        "story_upsert": (
            {"story": "8831189b"},
            _story().model_dump(mode="json", exclude={"subtasks"}),
        ),
        "subtask_upsert": (
            {"story": "8831189b"},
            _subtask("ef248597", base="m1/task-fdebc746").model_dump(
                mode="json", exclude={"phases"}
            ),
        ),
        "phase_upsert": (
            {"story": "8831189b", "card": "ef248597"},
            models.PhaseRun(name="implement", kind="agent", status="started").model_dump(
                mode="json", exclude={"attempts"}
            ),
        ),
    }[event]
    st = store.Store.open(repo, RUN_ID)
    try:
        _record_full_run(st, repo)
        _append_raw(
            st.journal,
            {
                "seq": st.journal.last_seq() + 1,
                "ts": "2026-09-23T10:20:00+00:00",
                "run_id": RUN_ID,
                "event": event,
                **coordinates,
                "payload": {**payload, "cost": None},
            },
        )
        with pytest.raises(ValidationError) as excinfo:
            st.replay_journal(RUN_ID)
    finally:
        st.close()

    assert [error["loc"] for error in excinfo.value.errors()] == [("cost",)]


def test_a_line_with_an_invalid_status_raises_out_of_rebuild(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        _record_full_run(st, repo)
        journal = st.journal
        _append_raw(
            journal,
            {
                "seq": journal.last_seq() + 1,
                "ts": "2026-09-23T10:20:00+00:00",
                "run_id": RUN_ID,
                "event": "story_upsert",
                "story": "8831189b",
                "card": None,
                "phase": None,
                "attempt": None,
                "payload": {
                    "card_id": "8831189b",
                    "title": "Foundations",
                    "level": 0,
                    "status": "finished",
                    "tip_branch": None,
                },
            },
        )
        with pytest.raises(ValidationError) as excinfo:
            st.rebuild_from_journal(RUN_ID)
    finally:
        st.close()
    message = str(excinfo.value)
    for allowed in ("pending", "started", "done", "failed", "escalated"):
        assert allowed in message


def test_a_corrupt_line_raises_out_of_rebuild_naming_the_journal(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        _record_full_run(st, repo)
        with st.journal.path.open("a", encoding="utf-8") as handle:
            handle.write("{not json at all\n")
        with pytest.raises(store.CorruptJournalError) as excinfo:
            st.rebuild_from_journal(RUN_ID)
        message = str(excinfo.value)
        assert str(st.journal.path) in message
    finally:
        st.close()


def test_rebuilding_a_run_with_no_journal_raises(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        with pytest.raises(store.MissingJournalError) as excinfo:
            st.rebuild_from_journal("run-never-started")
        assert st.load_run("run-never-started") is None
    finally:
        st.close()
    assert "run-never-started" in str(excinfo.value)


def test_a_journal_whose_head_is_missing_raises_a_journal_error(repo):
    # Review Focus 1: a story event with no run_upsert before it must name the
    # problem, not fail with an AttributeError on None.
    journal = store.Journal(RUN_ID)
    journal.append(
        "story_upsert",
        _story().model_dump(mode="json", exclude={"subtasks"}),
        story="8831189b",
    )
    st = store.Store.open(repo, RUN_ID)
    try:
        with pytest.raises(store.JournalError) as excinfo:
            st.rebuild_from_journal(RUN_ID)
    finally:
        st.close()
    assert "run_upsert" in str(excinfo.value)


def test_a_line_naming_an_unknown_parent_raises_a_journal_error(repo):
    journal = store.Journal(RUN_ID)
    journal.append("run_upsert", _run(repo).model_dump(mode="json", exclude={"stories"}))
    journal.append(
        "subtask_upsert",
        _subtask().model_dump(mode="json", exclude={"phases"}),
        story="never-recorded",
        card="ef248597",
    )
    st = store.Store.open(repo, RUN_ID)
    try:
        with pytest.raises(store.JournalError) as excinfo:
            st.rebuild_from_journal(RUN_ID)
    finally:
        st.close()
    assert "never-recorded" in str(excinfo.value)


def test_rebuilding_one_run_leaves_another_runs_rows_alone(repo):
    # Review Focus 4: one project DB holds every run. A rebuild is scoped.
    other_id = "run-2026-09-22-07"
    first = store.Store.open(repo, RUN_ID)
    try:
        _record_full_run(first, repo)
    finally:
        first.close()

    second = store.Store.open(repo, other_id)
    try:
        second.record_run(_run(repo, run_id=other_id))
        second.record_story(_story())
        second.record_subtask("8831189b", _subtask("aaaa1111"))
        untouched = second.load_run(other_id)
    finally:
        second.close()

    rebuilding = store.Store.open(repo, RUN_ID)
    try:
        rebuilding.rebuild_from_journal(RUN_ID)
        assert rebuilding.load_run(other_id) == untouched
        assert rebuilding.load_run(RUN_ID) is not None
    finally:
        rebuilding.close()


def test_a_status_transition_on_a_parent_keeps_the_children_recorded_before_it(repo):
    # §9: a transition is the same node journalled again. Replaying the later
    # line must not drop the phases and attempts that were journalled between
    # the two, or a rebuild would silently amputate the in-flight subtask.
    st = store.Store.open(repo, RUN_ID)
    try:
        _record_full_run(st, repo)
        st.record_story(_story().model_copy(update={"status": "done"}))
        st.record_subtask(
            "8831189b",
            _subtask("ef248597", base="m1/task-fdebc746").model_copy(
                update={"status": "done"}
            ),
        )
        st.record_phase(
            "8831189b",
            "ef248597",
            models.PhaseRun(
                name="implement",
                kind="agent",
                status="done",
                started_at=datetime(2026, 9, 23, 10, 13, tzinfo=timezone.utc),
                ended_at=datetime(2026, 9, 23, 10, 30, tzinfo=timezone.utc),
            ),
        )
        run = st.rebuild_from_journal(RUN_ID)
    finally:
        st.close()

    story = run.stories[0]
    assert story.status == "done"
    assert [subtask.card_id for subtask in story.subtasks] == ["fdebc746", "ef248597"]

    subtask = story.subtasks[1]
    assert subtask.status == "done"
    assert [phase.name for phase in subtask.phases] == ["explore", "implement"]

    implement = subtask.phases[1]
    assert implement.status == "done"
    assert [attempt.n for attempt in implement.attempts] == [1]


def test_read_returns_lines_in_sequence_order_not_file_order(repo):
    # A line can reach the file out of order (two writers, a partial flush).
    # `seq` is the ordering, so `read` sorts by it and `replay` folds in that
    # order rather than in the order the bytes happen to sit on disk.
    journal = store.Journal(RUN_ID)
    journal.append("run_upsert", _run(repo).model_dump(mode="json", exclude={"stories"}))
    _append_raw(
        journal,
        {
            "seq": 3,
            "ts": "2026-09-23T10:20:00+00:00",
            "run_id": RUN_ID,
            "event": "story_upsert",
            "story": "8831189b",
            "payload": _story().model_dump(mode="json", exclude={"subtasks"})
            | {"status": "done"},
        },
    )
    _append_raw(
        journal,
        {
            "seq": 2,
            "ts": "2026-09-23T10:10:00+00:00",
            "run_id": RUN_ID,
            "event": "story_upsert",
            "story": "8831189b",
            "payload": _story().model_dump(mode="json", exclude={"subtasks"})
            | {"status": "started"},
        },
    )

    assert [line.seq for line in journal.read()] == [1, 2, 3]

    st = store.Store.open(repo, RUN_ID)
    try:
        run = st.rebuild_from_journal(RUN_ID)
    finally:
        st.close()
    assert run.stories[0].status == "done"


def test_recording_a_run_whose_id_is_not_the_stores_run_id_is_refused(repo):
    # The row is keyed by the store's run id while the journal payload carries
    # the model's own. Letting the two differ makes `rebuild_from_journal`
    # return a run that `load_run` can never equal, so it is refused outright.
    st = store.Store.open(repo, RUN_ID)
    try:
        with pytest.raises(ValueError) as excinfo:
            st.record_run(_run(repo, run_id="run-somewhere-else"))
        message = str(excinfo.value)
        assert RUN_ID in message
        assert "run-somewhere-else" in message
        assert st.load_run(RUN_ID) is None
    finally:
        st.close()
    with pytest.raises(store.MissingJournalError):
        store.Journal(RUN_ID).read()


def test_a_journal_whose_run_upsert_names_another_run_raises(repo):
    journal = store.Journal(RUN_ID)
    journal.append(
        "run_upsert",
        _run(repo, run_id="run-somewhere-else").model_dump(
            mode="json", exclude={"stories"}
        ),
    )
    st = store.Store.open(repo, RUN_ID)
    try:
        with pytest.raises(store.JournalError) as excinfo:
            st.rebuild_from_journal(RUN_ID)
        assert st.load_run(RUN_ID) is None
    finally:
        st.close()
    message = str(excinfo.value)
    assert RUN_ID in message
    assert "run-somewhere-else" in message


def _record_summary(
    root: Path,
    run_id: str,
    started_at: datetime | None,
    *,
    workflow: str = "milestone",
    milestone_id: str | None = None,
    subtask_cards: tuple[str, ...] = (),
) -> None:
    """One run row in `root`'s projection, plus, when `subtask_cards` is given,
    one story holding those subtasks in that order. The defaults write the run
    row alone, as every older caller expects."""
    opened = store.Store.open(root, run_id)
    try:
        opened.record_run(
            _run(root, run_id).model_copy(
                update={
                    "started_at": started_at,
                    "workflow": workflow,
                    "milestone_id": milestone_id,
                }
            )
        )
        if subtask_cards:
            opened.record_story(_story())
            for card in subtask_cards:
                opened.record_subtask(_story().card_id, _subtask(card))
    finally:
        opened.close()


def test_list_runs_returns_this_projects_runs_newest_first(repo, tmp_path):
    """The `runs` table is shared by every run of one project, so the listing is
    a read of that table alone -- and a second project is a second database file,
    which this one must not see."""
    _record_summary(repo, "run-a", datetime(2026, 9, 21, 9, 0, tzinfo=timezone.utc))
    _record_summary(repo, "run-b", datetime(2026, 9, 23, 9, 0, tzinfo=timezone.utc))
    other = tmp_path / "other-repo"
    other.mkdir()
    _record_summary(other, "run-elsewhere", datetime(2026, 9, 24, 9, 0, tzinfo=timezone.utc))

    conn = store.open_db(repo)
    try:
        summaries = store.list_runs(conn)
    finally:
        conn.close()

    assert [summary.id for summary in summaries] == ["run-b", "run-a"]
    assert summaries[0].workflow == "milestone"
    assert summaries[0].status == "started"
    assert summaries[0].started_at == datetime(2026, 9, 23, 9, 0, tzinfo=timezone.utc)


def test_list_runs_on_a_project_with_no_runs_is_empty(repo):
    conn = store.open_db(repo)
    try:
        assert store.list_runs(conn) == []
    finally:
        conn.close()


def test_list_runs_puts_a_run_with_no_start_time_last(repo):
    """`runs.started_at` is nullable, so a row without one must still be listed."""
    _record_summary(repo, "run-dated", datetime(2026, 9, 23, 9, 0, tzinfo=timezone.utc))
    _record_summary(repo, "run-undated", None)

    conn = store.open_db(repo)
    try:
        summaries = store.list_runs(conn)
    finally:
        conn.close()

    assert [summary.id for summary in summaries] == ["run-dated", "run-undated"]
    assert summaries[-1].started_at is None


def test_list_runs_breaks_a_started_at_tie_with_the_run_id(repo):
    """Run ids are minted at second resolution, so two runs of one project can
    share a `started_at` and the order must still be total."""
    same = datetime(2026, 9, 23, 9, 0, tzinfo=timezone.utc)
    _record_summary(repo, "run-a", same)
    _record_summary(repo, "run-b", same)

    conn = store.open_db(repo)
    try:
        assert [summary.id for summary in store.list_runs(conn)] == ["run-b", "run-a"]
    finally:
        conn.close()


SUMMARY_KEYS = {
    "id",
    "workflow",
    "repo_dir",
    "base_branch",
    "branch_prefix",
    "status",
    "started_at",
    "milestone_id",
    "card_id",
    "lease",
    "progress",
}
"""The seven names `am runs` always had, plus `milestone_id` and `card_id`
(card 0b5a15d7), `lease` (card 6bf47e74) and `progress` (card 882b212b)."""


def _listed(root: Path) -> list[store.RunSummary]:
    conn = store.open_db(root)
    try:
        return store.list_runs(conn)
    finally:
        conn.close()


def test_list_runs_gives_a_milestone_run_its_milestone_id_and_no_card_id(repo):
    _record_summary(repo, "run-m", None, milestone_id=MILESTONE_ID)

    [summary] = _listed(repo)

    assert summary.milestone_id == MILESTONE_ID
    assert summary.card_id is None


def test_list_runs_gives_a_card_run_its_card_id_and_no_milestone_id(repo):
    _record_summary(repo, "run-t", None, workflow="task", subtask_cards=("ef248597",))

    [summary] = _listed(repo)

    assert summary.workflow == "task"
    assert summary.milestone_id is None
    assert summary.card_id == "ef248597"


def test_list_runs_gives_a_task_run_with_no_subtask_row_no_card_id(repo):
    """A `--card` run's row is written before its subtask row, so a reader can
    see it in between; that is not an error."""
    _record_summary(repo, "run-t", None, workflow="task")

    [summary] = _listed(repo)

    assert summary.card_id is None
    assert summary.milestone_id is None


def test_list_runs_never_gives_a_milestone_run_a_card_id_even_with_subtask_rows(repo):
    _record_summary(
        repo,
        "run-m",
        None,
        milestone_id=MILESTONE_ID,
        subtask_cards=("ef248597", "1535b285"),
    )

    [summary] = _listed(repo)

    assert summary.milestone_id == MILESTONE_ID
    assert summary.card_id is None


def test_list_runs_gives_each_task_run_its_own_card_id(repo):
    _record_summary(
        repo,
        "run-a",
        datetime(2026, 9, 21, 9, 0, tzinfo=timezone.utc),
        workflow="task",
        subtask_cards=("aaaa1111",),
    )
    _record_summary(
        repo,
        "run-b",
        datetime(2026, 9, 23, 9, 0, tzinfo=timezone.utc),
        workflow="task",
        subtask_cards=("bbbb2222",),
    )

    summaries = _listed(repo)

    assert [(s.id, s.card_id) for s in summaries] == [
        ("run-b", "bbbb2222"),
        ("run-a", "aaaa1111"),
    ]


def test_list_runs_picks_the_first_subtask_when_a_task_run_has_several(repo):
    """A `--card` run records one subtask, but a projection holding two must
    still list the run, with a stable answer: the lowest position wins."""
    _record_summary(
        repo, "run-t", None, workflow="task", subtask_cards=("zzzz9999", "aaaa1111")
    )

    [summary] = _listed(repo)

    assert summary.card_id == "zzzz9999"


def test_list_runs_breaks_a_subtask_position_tie_with_the_lowest_card_id(repo):
    """Positions count per story, so a `task` run holding subtasks under two
    stories has two rows at position 0; the lower card id must win, whatever
    order the stories sort in."""
    opened = store.Store.open(repo, "run-t")
    try:
        opened.record_run(
            _run(repo, "run-t").model_copy(update={"started_at": None, "workflow": "task"})
        )
        early_story = _story().model_copy(update={"card_id": "00000000"})
        opened.record_story(early_story)
        opened.record_subtask(early_story.card_id, _subtask("zzzz9999"))
        opened.record_story(_story())
        opened.record_subtask(_story().card_id, _subtask("aaaa1111"))
    finally:
        opened.close()

    [summary] = _listed(repo)

    assert summary.card_id == "aaaa1111"


def test_run_summary_fields_are_the_old_seven_plus_milestone_id_card_id_lease_and_progress():
    assert set(store.RunSummary.model_fields) == SUMMARY_KEYS
    assert store.RunSummary.model_config["extra"] == "forbid"
    assert store.RunSummary.model_fields["milestone_id"].default is None
    assert store.RunSummary.model_fields["card_id"].default is None
    assert store.RunSummary.model_fields["lease"].default is None
    assert store.RunSummary.model_fields["progress"].default is None


def test_a_listed_run_dumps_exactly_the_summary_keys(repo):
    _record_summary(repo, "run-t", None, workflow="task", subtask_cards=("ef248597",))

    [summary] = _listed(repo)

    assert set(summary.model_dump()) == SUMMARY_KEYS


RUN_LEASE_KEYS = {"live", "pid", "host", "heartbeat_at", "accepting"}
"""The `runs[]` lease object: `am status`'s `control.lease` minus `acquired_at`."""


def _summary_fields(**overrides) -> dict:
    """The fields of one valid `RunSummary`, without the database."""
    return {
        "id": "run-x",
        "workflow": "task",
        "repo_dir": Path("/repo"),
        "base_branch": "main",
        "branch_prefix": "m1",
        "status": "started",
        **overrides,
    }


def _run_lease_fields(**overrides) -> dict:
    """One valid `RunLease` as a plain dict."""
    return {
        "live": True,
        "pid": 4242,
        "host": "box",
        "heartbeat_at": "2026-09-29T09:00:00+00:00",
        "accepting": True,
        **overrides,
    }


def test_run_lease_has_exactly_the_five_keys_and_forbids_others():
    assert set(store.RunLease.model_fields) == RUN_LEASE_KEYS
    assert store.RunLease.model_config["extra"] == "forbid"


def test_run_summary_lease_defaults_to_none():
    summary = store.RunSummary.model_validate(_summary_fields())

    assert summary.lease is None
    assert summary.model_dump()["lease"] is None


def test_run_summary_accepts_a_lease_object_and_dumps_it_as_a_plain_dict():
    summary = store.RunSummary.model_validate(_summary_fields(lease=_run_lease_fields()))

    assert isinstance(summary.lease, store.RunLease)
    assert summary.model_dump()["lease"] == _run_lease_fields()


def test_run_summary_accepts_null_pid_host_and_heartbeat_in_a_lease():
    """The source design types these three as nullable; the model must agree."""
    summary = store.RunSummary.model_validate(
        _summary_fields(lease=_run_lease_fields(pid=None, host=None, heartbeat_at=None))
    )

    assert summary.lease is not None
    assert (summary.lease.pid, summary.lease.host, summary.lease.heartbeat_at) == (
        None,
        None,
        None,
    )


def test_run_summary_rejects_an_unknown_key_inside_the_lease():
    """The error must sit at `lease.acquired_at`: a `RunSummary` with no
    `lease` field at all would also raise, but at `lease`, the wrong reason."""
    with pytest.raises(ValidationError) as caught:
        store.RunSummary.model_validate(
            _summary_fields(
                lease=_run_lease_fields(acquired_at="2026-09-29T08:59:00+00:00")
            )
        )

    [error] = caught.value.errors()
    assert error["loc"] == ("lease", "acquired_at")
    assert error["type"] == "extra_forbidden"


def test_run_summary_rejects_a_lease_missing_a_key():
    fields = _run_lease_fields()
    del fields["accepting"]

    with pytest.raises(ValidationError) as caught:
        store.RunSummary.model_validate(_summary_fields(lease=fields))

    [error] = caught.value.errors()
    assert error["loc"] == ("lease", "accepting")
    assert error["type"] == "missing"


PROGRESS_KEYS = {"stories", "subtasks", "current"}
PROGRESS_COUNT_KEYS = {"done", "total"}
PROGRESS_CURRENT_KEYS = {"card", "phase", "attempt"}
"""The `runs[]` progress object (card 882b212b): two counts and the step in flight."""


def _progress_fields(**overrides) -> dict:
    """One valid `RunProgress` as a plain dict."""
    return {
        "stories": {"done": 1, "total": 2},
        "subtasks": {"done": 3, "total": 5},
        "current": {"card": "card-1", "phase": "implement", "attempt": 2},
        **overrides,
    }


def test_run_progress_models_have_exactly_their_keys_and_forbid_others():
    assert set(store.RunProgress.model_fields) == PROGRESS_KEYS
    assert set(store.ProgressCount.model_fields) == PROGRESS_COUNT_KEYS
    assert set(store.ProgressCurrent.model_fields) == PROGRESS_CURRENT_KEYS
    for model in (store.RunProgress, store.ProgressCount, store.ProgressCurrent):
        assert model.model_config["extra"] == "forbid"


def test_run_summary_progress_defaults_to_none():
    """Additive for anyone who builds a `RunSummary` by hand; `list_runs`
    itself always fills it."""
    summary = store.RunSummary.model_validate(_summary_fields())

    assert summary.progress is None
    assert summary.model_dump()["progress"] is None


def test_run_summary_accepts_a_progress_object_and_dumps_it_as_a_plain_dict():
    summary = store.RunSummary.model_validate(_summary_fields(progress=_progress_fields()))

    assert isinstance(summary.progress, store.RunProgress)
    assert isinstance(summary.progress.current, store.ProgressCurrent)
    assert summary.model_dump()["progress"] == _progress_fields()


def test_run_summary_accepts_a_null_current_and_a_null_attempt():
    no_current = store.RunSummary.model_validate(
        _summary_fields(progress=_progress_fields(current=None))
    )
    no_attempt = store.RunSummary.model_validate(
        _summary_fields(
            progress=_progress_fields(
                current={"card": "card-1", "phase": "explore", "attempt": None}
            )
        )
    )

    assert no_current.progress is not None
    assert no_current.progress.current is None
    assert no_attempt.progress is not None
    assert no_attempt.progress.current is not None
    assert no_attempt.progress.current.attempt is None


@pytest.mark.parametrize(
    "progress, loc",
    [
        (_progress_fields(extra=1), ("progress", "extra")),
        (
            _progress_fields(stories={"done": 0, "total": 0, "failed": 0}),
            ("progress", "stories", "failed"),
        ),
        (
            _progress_fields(
                current={"card": "c", "phase": "p", "attempt": 1, "story": "s"}
            ),
            ("progress", "current", "story"),
        ),
    ],
    ids=["progress", "count", "current"],
)
def test_run_summary_rejects_an_unknown_key_anywhere_in_progress(progress, loc):
    with pytest.raises(ValidationError) as caught:
        store.RunSummary.model_validate(_summary_fields(progress=progress))

    [error] = caught.value.errors()
    assert error["loc"] == loc
    assert error["type"] == "extra_forbidden"


def test_run_summary_rejects_a_progress_missing_current():
    """`current` is required, never silently absent: `null` is the only way
    to say nothing is in flight."""
    fields = _progress_fields()
    del fields["current"]

    with pytest.raises(ValidationError) as caught:
        store.RunSummary.model_validate(_summary_fields(progress=fields))

    [error] = caught.value.errors()
    assert error["loc"] == ("progress", "current")
    assert error["type"] == "missing"


def test_list_runs_leaves_the_lease_to_the_caller_even_with_a_lease_row(repo):
    """`live` needs `control`, which `store` must not import, so `list_runs`
    never fills `lease`: `am runs` does, in `cli`."""
    _record_summary(repo, "run-l", None, workflow="task")
    _plant_lease(repo, "run-l", token="life-1")

    [summary] = _listed(repo)

    assert summary.lease is None


def test_store_does_not_import_control():
    """`control` imports `store`; the reverse would be a circular import.

    A top-level import would already break collection, so this walks the
    whole AST: a function-local `from . import control` or a multi-name
    `from agent_manager import models, control` must be caught too.
    """
    tree = ast.parse(Path(store.__file__).read_text())
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = "agent_manager" if node.level else (node.module or "")
            if node.level and node.module:
                base = f"agent_manager.{node.module}"
            imported.add(base)
            imported.update(f"{base}.{alias.name}" for alias in node.names)

    assert not {
        name
        for name in imported
        if name == "agent_manager.control" or name.startswith("agent_manager.control.")
    }


# -- progress (card 882b212b) -------------------------------------------------
#
# Unit tier: a SQLite fixture store in tmp_path, nothing spawned.


def _progress_run(
    root: Path,
    run_id: str,
    *,
    workflow: str = "milestone",
    status: str = "started",
    started_at: datetime | None = None,
) -> store.Store:
    """An open store for `run_id` holding its run row and nothing below it.
    The caller records the tree and closes the store."""
    opened = store.Store.open(root, run_id)
    opened.record_run(
        _run(root, run_id).model_copy(
            update={"workflow": workflow, "status": status, "started_at": started_at}
        )
    )
    return opened


def _tree_story(card_id: str, status: str = "started", title: str = "A story") -> models.StoryRun:
    return models.StoryRun(card_id=card_id, title=title, level=0, status=status)


def _tree_subtask(card_id: str, status: str = "started") -> models.SubtaskRun:
    return models.SubtaskRun(
        card_id=card_id, branch=f"m1/task-{card_id}", base_branch="main", status=status
    )


def _tree_phase(
    name: str, status: str, started_at: datetime | None = None
) -> models.PhaseRun:
    return models.PhaseRun(name=name, kind="agent", status=status, started_at=started_at)


def _tree_attempt(card: str, phase: str, n: int) -> models.Attempt:
    return models.Attempt(n=n, dispatch=_dispatch(card, phase, n))


def _progress_at(minute: int) -> datetime:
    return datetime(2026, 10, 4, 9, minute, tzinfo=timezone.utc)


def _progress_of(root: Path) -> dict[str, dict | None]:
    """Each listed run's `progress`, dumped, keyed by run id."""
    return {
        summary.id: None if summary.progress is None else summary.progress.model_dump()
        for summary in _listed(root)
    }


def _expected(
    stories: tuple[int, int] = (0, 0),
    subtasks: tuple[int, int] = (0, 0),
    current: dict | None = None,
) -> dict:
    """A dumped `RunProgress` from `(done, total)` pairs."""
    return {
        "stories": {"done": stories[0], "total": stories[1]},
        "subtasks": {"done": subtasks[0], "total": subtasks[1]},
        "current": current,
    }


def test_list_runs_gives_a_run_with_no_tree_rows_zero_progress_and_no_current(repo):
    """`progress` is never null from `list_runs`: a run row with nothing below
    it is 0 of 0 at both levels with no current step."""
    _progress_run(repo, "run-bare").close()

    [summary] = _listed(repo)

    assert summary.progress is not None
    assert summary.progress.current is None
    assert summary.progress.model_dump() == _expected()


def test_list_runs_counts_stories_and_subtasks_done_against_total(repo):
    """Only `done` is done: every other status counts toward `total` alone."""
    layout = {
        ("s-done", "done"): [("a", "done"), ("b", "done")],
        ("s-started", "started"): [("c", "started"), ("d", "stopped")],
        ("s-failed", "failed"): [("e", "failed")],
        ("s-escalated", "escalated"): [("f", "cancelled")],
        ("s-pending", "pending"): [("g", "pending")],
    }
    opened = _progress_run(repo, "run-m")
    try:
        for (story_id, story_status), subtasks in layout.items():
            opened.record_story(_tree_story(story_id, story_status))
            for card, status in subtasks:
                opened.record_subtask(story_id, _tree_subtask(card, status))
    finally:
        opened.close()

    assert _progress_of(repo) == {"run-m": _expected(stories=(1, 5), subtasks=(2, 7))}


def test_list_runs_counts_a_task_run_as_one_story_and_one_subtask(repo):
    """Re-recording a node upserts its row, so finishing the card moves `done`
    without growing `total`."""
    opened = _progress_run(repo, "run-t", workflow="task")
    try:
        opened.record_story(_tree_story("story-1"))
        opened.record_subtask("story-1", _tree_subtask("ef248597"))
        assert _progress_of(repo) == {
            "run-t": _expected(stories=(0, 1), subtasks=(0, 1))
        }

        opened.record_subtask("story-1", _tree_subtask("ef248597", "done"))
        opened.record_story(_tree_story("story-1", "done"))
    finally:
        opened.close()

    assert _progress_of(repo) == {"run-t": _expected(stories=(1, 1), subtasks=(1, 1))}


def test_list_runs_counts_the_integrate_story_and_its_resolver_subtasks(repo):
    """`store` cannot import `integration` (which imports `store`), so the
    synthetic Integrate story is a story like any other: a run that resolved
    a conflict shows one story more than its milestone has. Its resolver
    subtask is named after the conflicting story, as `integration` records it."""
    opened = _progress_run(repo, "run-m")
    try:
        opened.record_story(_tree_story("s1", "done"))
        opened.record_subtask("s1", _tree_subtask("a", "done"))
        opened.record_story(_tree_story("integrate", "started", title="Integrate"))
        opened.record_subtask("integrate", _tree_subtask("s1"))
        opened.record_phase(
            "integrate", "s1", _tree_phase("resolve", "started", _progress_at(5))
        )
    finally:
        opened.close()

    assert _progress_of(repo) == {
        "run-m": _expected(
            stories=(1, 2),
            subtasks=(1, 2),
            current={"card": "s1", "phase": "resolve", "attempt": None},
        )
    }


def test_list_runs_keeps_each_runs_progress_to_its_own_rows(repo):
    """Both runs use the same story, card and phase names, and the later run's
    phase started later with more attempts: none of it may leak across."""
    early = _progress_run(repo, "run-a", started_at=_progress_at(0))
    try:
        early.record_story(_tree_story("story-1", "done"))
        early.record_subtask("story-1", _tree_subtask("card-1", "done"))
        early.record_subtask("story-1", _tree_subtask("card-2"))
        early.record_phase(
            "story-1", "card-2", _tree_phase("implement", "started", _progress_at(1))
        )
        early.record_attempt(
            "story-1", "card-2", "implement", _tree_attempt("card-2", "implement", 1)
        )
    finally:
        early.close()
    late = _progress_run(repo, "run-b", started_at=_progress_at(10))
    try:
        late.record_story(_tree_story("story-1"))
        late.record_story(_tree_story("story-2"))
        late.record_subtask("story-1", _tree_subtask("card-2"))
        late.record_phase(
            "story-1", "card-2", _tree_phase("implement", "started", _progress_at(30))
        )
        for n in (1, 2, 3):
            late.record_attempt(
                "story-1", "card-2", "implement", _tree_attempt("card-2", "implement", n)
            )
    finally:
        late.close()

    assert _progress_of(repo) == {
        "run-a": _expected(
            stories=(1, 1),
            subtasks=(1, 2),
            current={"card": "card-2", "phase": "implement", "attempt": 1},
        ),
        "run-b": _expected(
            stories=(0, 2),
            subtasks=(0, 1),
            current={"card": "card-2", "phase": "implement", "attempt": 3},
        ),
    }


def test_an_empty_history_still_lists_no_runs_with_progress(repo):
    """No new error path: an empty projection is still an empty list."""
    assert _listed(repo) == []


def test_list_runs_current_is_the_started_phase_with_its_latest_attempt(repo):
    """`attempt` is the highest `n` of that phase of that card: not of a done
    phase before it, and not of the same phase name on another card."""
    opened = _progress_run(repo, "run-m")
    try:
        opened.record_story(_tree_story("story-1"))
        opened.record_subtask("story-1", _tree_subtask("card-1"))
        opened.record_subtask("story-1", _tree_subtask("card-2", "done"))
        opened.record_phase(
            "story-1", "card-1", _tree_phase("explore", "done", _progress_at(1))
        )
        for n in (1, 2, 3, 4):
            opened.record_attempt(
                "story-1", "card-1", "explore", _tree_attempt("card-1", "explore", n)
            )
        opened.record_phase(
            "story-1", "card-2", _tree_phase("implement", "done", _progress_at(2))
        )
        for n in range(1, 8):
            opened.record_attempt(
                "story-1", "card-2", "implement", _tree_attempt("card-2", "implement", n)
            )
        opened.record_phase(
            "story-1", "card-1", _tree_phase("implement", "started", _progress_at(3))
        )
        for n in (1, 2):
            opened.record_attempt(
                "story-1", "card-1", "implement", _tree_attempt("card-1", "implement", n)
            )
    finally:
        opened.close()

    [summary] = _listed(repo)

    assert summary.progress is not None
    assert summary.progress.current == store.ProgressCurrent(
        card="card-1", phase="implement", attempt=2
    )


def test_list_runs_current_attempt_is_null_before_any_attempt_row(repo):
    opened = _progress_run(repo, "run-t", workflow="task")
    try:
        opened.record_story(_tree_story("story-1"))
        opened.record_subtask("story-1", _tree_subtask("card-1"))
        opened.record_phase(
            "story-1", "card-1", _tree_phase("explore", "started", _progress_at(1))
        )
    finally:
        opened.close()

    assert _progress_of(repo)["run-t"]["current"] == {
        "card": "card-1",
        "phase": "explore",
        "attempt": None,
    }


def test_list_runs_current_is_null_when_no_phase_is_started(repo):
    """A phase that finished is re-recorded `done` in place, so it drops out;
    a `pending` phase is not in flight."""
    opened = _progress_run(repo, "run-t", workflow="task")
    try:
        opened.record_story(_tree_story("story-1"))
        opened.record_subtask("story-1", _tree_subtask("card-1"))
        opened.record_phase(
            "story-1", "card-1", _tree_phase("explore", "started", _progress_at(1))
        )
        opened.record_attempt(
            "story-1", "card-1", "explore", _tree_attempt("card-1", "explore", 1)
        )
        opened.record_phase(
            "story-1", "card-1", _tree_phase("explore", "done", _progress_at(1))
        )
        opened.record_phase("story-1", "card-1", _tree_phase("verify", "pending"))
    finally:
        opened.close()

    assert _progress_of(repo) == {"run-t": _expected(stories=(0, 1), subtasks=(0, 1))}


def test_list_runs_current_picks_the_latest_started_phase_among_parallel_ones(repo):
    """A milestone run has subtasks of several stories in flight at once; the
    one whose phase started last is `current`, whatever order they were
    recorded in."""
    opened = _progress_run(repo, "run-m")
    try:
        for story_id, card, minute in (("s1", "c1", 5), ("s2", "c2", 9), ("s3", "c3", 7)):
            opened.record_story(_tree_story(story_id))
            opened.record_subtask(story_id, _tree_subtask(card))
            opened.record_phase(
                story_id, card, _tree_phase("implement", "started", _progress_at(minute))
            )
    finally:
        opened.close()

    assert _progress_of(repo)["run-m"]["current"] == {
        "card": "c2",
        "phase": "implement",
        "attempt": None,
    }


def test_list_runs_current_breaks_a_started_at_tie_by_story_then_card_then_position(repo):
    """The keys are ranked, not just present: in `run-story` the winning story
    holds the card id that sorts last, and in `run-card` the winning card's
    started phase sits at the higher position, so any other key order picks
    the other phase. `run-story` and `run-card` record the loser first, so
    insertion order cannot pass for the tie-break. `position` is assigned in
    insertion order, so in `run-position` the winner is recorded first, and
    the names are chosen so that ordering by name would pick the other."""
    same = _progress_at(5)
    by_story = _progress_run(repo, "run-story")
    try:
        for story_id, card in (("bbbb", "c-a"), ("aaaa", "c-z")):
            by_story.record_story(_tree_story(story_id))
            by_story.record_subtask(story_id, _tree_subtask(card))
            by_story.record_phase(story_id, card, _tree_phase("implement", "started", same))
    finally:
        by_story.close()
    by_card = _progress_run(repo, "run-card")
    try:
        by_card.record_story(_tree_story("story-1"))
        by_card.record_subtask("story-1", _tree_subtask("c2"))
        by_card.record_phase("story-1", "c2", _tree_phase("implement", "started", same))
        by_card.record_subtask("story-1", _tree_subtask("c1"))
        by_card.record_phase("story-1", "c1", _tree_phase("explore", "done", same))
        by_card.record_phase("story-1", "c1", _tree_phase("implement", "started", same))
    finally:
        by_card.close()
    by_position = _progress_run(repo, "run-position")
    try:
        by_position.record_story(_tree_story("story-1"))
        by_position.record_subtask("story-1", _tree_subtask("card-1"))
        for name in ("zeta", "alpha"):
            by_position.record_phase("story-1", "card-1", _tree_phase(name, "started", same))
    finally:
        by_position.close()

    currents = {run_id: progress["current"] for run_id, progress in _progress_of(repo).items()}

    assert currents == {
        "run-story": {"card": "c-z", "phase": "implement", "attempt": None},
        "run-card": {"card": "c1", "phase": "implement", "attempt": None},
        "run-position": {"card": "card-1", "phase": "zeta", "attempt": None},
    }


def test_list_runs_current_takes_a_started_phase_with_no_start_time_only_when_alone(repo):
    """`started_at DESC` sends NULL last: an undated started phase loses to any
    dated one, even one whose story sorts after it, and wins when alone."""
    mixed = _progress_run(repo, "run-mixed")
    try:
        mixed.record_story(_tree_story("a"))
        mixed.record_subtask("a", _tree_subtask("card-a"))
        mixed.record_phase("a", "card-a", _tree_phase("undated", "started", None))
        mixed.record_story(_tree_story("b"))
        mixed.record_subtask("b", _tree_subtask("card-b"))
        mixed.record_phase("b", "card-b", _tree_phase("dated", "started", _progress_at(1)))
    finally:
        mixed.close()
    alone = _progress_run(repo, "run-alone")
    try:
        alone.record_story(_tree_story("a"))
        alone.record_subtask("a", _tree_subtask("card-a"))
        alone.record_phase("a", "card-a", _tree_phase("undated", "started", None))
    finally:
        alone.close()

    currents = {run_id: progress["current"] for run_id, progress in _progress_of(repo).items()}

    assert currents == {
        "run-mixed": {"card": "card-b", "phase": "dated", "attempt": None},
        "run-alone": {"card": "card-a", "phase": "undated", "attempt": None},
    }


def test_list_runs_current_comes_from_rows_not_from_the_run_status(repo):
    """A run whose process died mid-phase keeps the `started` phase row it
    stopped in, and `current` shows it whatever the run row says; `lease.live`
    is what tells a consumer nobody is working on it."""
    opened = _progress_run(repo, "run-dead", status="failed")
    try:
        opened.record_story(_tree_story("story-1"))
        opened.record_subtask("story-1", _tree_subtask("card-1"))
        opened.record_phase(
            "story-1", "card-1", _tree_phase("implement", "started", _progress_at(1))
        )
        opened.record_attempt(
            "story-1", "card-1", "implement", _tree_attempt("card-1", "implement", 1)
        )
    finally:
        opened.close()

    [summary] = _listed(repo)

    assert summary.status == "failed"
    assert summary.progress is not None
    assert summary.progress.model_dump()["current"] == {
        "card": "card-1",
        "phase": "implement",
        "attempt": 1,
    }


def test_list_runs_progress_reads_without_writing(repo):
    """`am runs` takes no lock and writes nothing: the progress queries are
    plain `SELECT`s, and leave no transaction open behind them."""
    opened = _progress_run(repo, "run-t", workflow="task")
    try:
        opened.record_story(_tree_story("story-1"))
        opened.record_subtask("story-1", _tree_subtask("card-1"))
        opened.record_phase(
            "story-1", "card-1", _tree_phase("implement", "started", _progress_at(1))
        )
        opened.record_attempt(
            "story-1", "card-1", "implement", _tree_attempt("card-1", "implement", 1)
        )
    finally:
        opened.close()

    conn = store.open_db(repo)
    try:
        before = conn.total_changes
        [summary] = store.list_runs(conn)
        after = conn.total_changes
        in_transaction = conn.in_transaction
    finally:
        conn.close()

    assert summary.progress is not None
    assert summary.progress.current is not None
    assert after == before
    assert in_transaction is False


def test_latest_run_id_is_the_newest_recorded_run(repo):
    _record_summary(repo, "run-a", datetime(2026, 9, 21, 9, 0, tzinfo=timezone.utc))
    _record_summary(repo, "run-c", datetime(2026, 9, 24, 9, 0, tzinfo=timezone.utc))
    _record_summary(repo, "run-b", datetime(2026, 9, 23, 9, 0, tzinfo=timezone.utc))

    conn = store.open_db(repo)
    try:
        assert store.latest_run_id(conn) == "run-c"
        assert store.latest_run_id(conn) == store.list_runs(conn)[0].id
    finally:
        conn.close()


def test_latest_run_id_is_none_for_a_project_with_no_runs(repo):
    conn = store.open_db(repo)
    try:
        assert store.latest_run_id(conn) is None
    finally:
        conn.close()


def test_load_run_reads_the_tree_from_a_bare_connection(repo):
    """`status` has no run id until it has read the database, so it cannot use
    `Store.open(root, run_id)` -- and must not, since a `Journal` mkdirs a run
    directory for a run that may not exist."""
    opened = store.Store.open(repo, RUN_ID)
    try:
        opened.record_run(_run(repo))
        opened.record_story(
            models.StoryRun(card_id="story-1", title="A story", level=0, status="started")
        )
        opened.record_subtask(
            "story-1",
            models.SubtaskRun(
                card_id="card-1", branch="m1/task-x", base_branch="main", status="started"
            ),
        )
    finally:
        opened.close()

    conn = store.open_db(repo)
    try:
        run = store.load_run(conn, RUN_ID)
    finally:
        conn.close()

    assert run is not None
    assert run.id == RUN_ID
    assert [story.card_id for story in run.stories] == ["story-1"]
    assert [subtask.card_id for subtask in run.stories[0].subtasks] == ["card-1"]


def test_load_run_of_an_unknown_id_is_none_and_creates_no_run_directory(repo):
    conn = store.open_db(repo)
    try:
        assert store.load_run(conn, "run-that-never-was") is None
    finally:
        conn.close()

    assert not (paths.data_dir() / "runs" / "run-that-never-was").exists()


def _held_elsewhere(lock) -> bool:
    """True when a different thread cannot take `lock` right now.

    Probing from a second thread is what makes this meaningful for an RLock:
    the owning thread could always re-acquire it. Deterministic (P7): a
    non-blocking acquire either succeeds or it does not.
    """
    result: list[bool] = []

    def probe() -> None:
        acquired = lock.acquire(blocking=False)
        if acquired:
            lock.release()
        result.append(not acquired)

    prober = threading.Thread(target=probe)
    prober.start()
    prober.join()
    return result[0]


class _SpyingConnection:
    """Wraps a real connection so a test can observe the moment it is closed.

    `sqlite3.Connection.close` is read-only on the instance, so it cannot be
    monkeypatched directly; everything else is forwarded to the real one.
    """

    def __init__(self, real: sqlite3.Connection, on_close) -> None:
        self._real = real
        self._on_close = on_close

    def close(self) -> None:
        try:
            self._on_close()
        finally:
            self._real.close()

    def __getattr__(self, name: str):
        return getattr(self._real, name)


def test_every_record_holds_the_store_lock_across_the_journal_append_and_the_row_write(
    repo, monkeypatch
):
    # P2: the journal append and the row write are one critical section, so
    # journal order equals row order. Checked at the moment each happens.
    st = store.Store.open(repo, RUN_ID)
    seen: list[tuple[str, bool]] = []

    real_append = st.journal.append

    def spying_append(*args, **kwargs):
        seen.append(("journal", _held_elsewhere(st._lock)))
        return real_append(*args, **kwargs)

    monkeypatch.setattr(st.journal, "append", spying_append)

    for writer in (
        "_write_run_row",
        "_write_story_row",
        "_write_subtask_row",
        "_write_phase_row",
        "_write_attempt_row",
    ):
        real_writer = getattr(st, writer)

        def spying_writer(*args, _real=real_writer, _name=writer, **kwargs):
            seen.append((_name, _held_elsewhere(st._lock)))
            return _real(*args, **kwargs)

        monkeypatch.setattr(st, writer, spying_writer)

    try:
        st.record_run(_run(repo))
        st.record_story(_story())
        st.record_subtask("8831189b", _subtask())
        st.record_phase("8831189b", "ef248597", models.PhaseRun(name="implement", kind="agent"))
        st.record_attempt(
            "8831189b", "ef248597", "implement", models.Attempt(n=1, dispatch=_dispatch())
        )
        assert _held_elsewhere(st._lock) is False
    finally:
        st.close()

    assert seen == [
        ("journal", True),
        ("_write_run_row", True),
        ("journal", True),
        ("_write_story_row", True),
        ("journal", True),
        ("_write_subtask_row", True),
        ("journal", True),
        ("_write_phase_row", True),
        ("journal", True),
        ("_write_attempt_row", True),
    ]


def test_rebuild_and_load_run_hold_the_store_lock_on_the_shared_connection(
    repo, monkeypatch
):
    # Deliberate extension beyond the record_* methods: rebuild deletes and
    # rewrites rows on the shared connection, and load_run reads on it.
    st = store.Store.open(repo, RUN_ID)
    try:
        _record_full_run(st, repo)
        seen: list[tuple[str, bool]] = []

        real_read = st.journal.read

        def spying_read():
            seen.append(("read", _held_elsewhere(st._lock)))
            return real_read()

        monkeypatch.setattr(st.journal, "read", spying_read)

        real_delete = st._delete_run

        def spying_delete(run_id):
            seen.append(("_delete_run", _held_elsewhere(st._lock)))
            return real_delete(run_id)

        monkeypatch.setattr(st, "_delete_run", spying_delete)

        real_attempt_writer = st._write_attempt_row

        def spying_attempt_writer(*args, **kwargs):
            seen.append(("_write_attempt_row", _held_elsewhere(st._lock)))
            return real_attempt_writer(*args, **kwargs)

        monkeypatch.setattr(st, "_write_attempt_row", spying_attempt_writer)

        real_load_run = store.load_run

        def spying_load_run(conn, run_id):
            seen.append(("load_run", _held_elsewhere(st._lock)))
            return real_load_run(conn, run_id)

        monkeypatch.setattr(store, "load_run", spying_load_run)

        rebuilt = st.rebuild_from_journal(RUN_ID)
        loaded = st.load_run(RUN_ID)
        assert _held_elsewhere(st._lock) is False
    finally:
        st.close()

    assert rebuilt == loaded
    # The first `load_run` is the foreign-value check (divergence §3.6): it
    # reads the projection under the same lock, before anything is deleted.
    assert seen == [
        ("read", True),
        ("load_run", True),
        ("_delete_run", True),
        ("_write_attempt_row", True),
        ("_write_attempt_row", True),
        ("load_run", True),
    ]


def test_close_holds_the_store_lock(repo):
    # The connection is never closed under an in-flight record.
    st = store.Store.open(repo, RUN_ID)
    held: list[bool] = []
    st._conn = _SpyingConnection(st._conn, lambda: held.append(_held_elsewhere(st._lock)))

    st.close()

    assert held == [True]
    assert _held_elsewhere(st._lock) is False


def test_a_failed_row_write_releases_the_store_lock(repo):
    # §9: the journal line survives the failed row write, and the `with` block
    # releases the lock so the next record is not deadlocked.
    st = store.Store.open(repo, RUN_ID)
    st.record_run(_run(repo))
    st.close()

    with pytest.raises(sqlite3.Error):
        st.record_story(_story())

    assert _held_elsewhere(st._lock) is False
    lines = store.Journal(RUN_ID).read()
    assert [line.event for line in lines] == ["run_upsert", "story_upsert"]


def test_a_failed_journal_append_releases_the_store_lock_and_writes_no_row(
    repo, monkeypatch
):
    # Review Focus 1: the journal is written first, so when it fails there is
    # no row, the same exception propagates, and the lock is free.
    st = store.Store.open(repo, RUN_ID)
    try:
        st.record_run(_run(repo))

        def failing_append(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(st.journal, "append", failing_append)

        with pytest.raises(OSError, match="disk full"):
            st.record_story(_story())

        assert _held_elsewhere(st._lock) is False
        assert st.connection.execute("SELECT COUNT(*) FROM stories").fetchone()[0] == 0
    finally:
        st.close()


def test_a_refused_record_run_releases_the_store_lock(repo):
    # Review Focus 2: the run-id check now runs inside the lock.
    st = store.Store.open(repo, RUN_ID)
    try:
        with pytest.raises(ValueError):
            st.record_run(_run(repo, run_id="run-somewhere-else"))
        assert _held_elsewhere(st._lock) is False
    finally:
        st.close()


def test_a_failed_rebuild_releases_the_store_lock(repo):
    # Review Focus 3: a rebuild that raises must not leave every later record
    # deadlocked behind it.
    st = store.Store.open(repo, RUN_ID)
    try:
        _record_full_run(st, repo)
        with st.journal.path.open("a", encoding="utf-8") as handle:
            handle.write("{not json at all\n")

        with pytest.raises(store.CorruptJournalError):
            st.rebuild_from_journal(RUN_ID)

        assert _held_elsewhere(st._lock) is False
    finally:
        st.close()


def test_recording_on_a_store_closed_by_another_thread_raises_and_frees_the_lock(repo):
    # Review Focus 5: a worker that records after another thread closed the
    # store gets a sqlite3.Error, not a hang or a silent success.
    st = store.Store.open(repo, RUN_ID)
    st.record_run(_run(repo))
    st.close()

    errors: list[BaseException] = []

    def work() -> None:
        try:
            st.record_story(_story())
        except BaseException as error:  # inspected below
            errors.append(error)

    worker = threading.Thread(target=work)
    worker.start()
    worker.join()

    assert len(errors) == 1
    assert isinstance(errors[0], sqlite3.Error)
    assert _held_elsewhere(st._lock) is False


STRESS_WORKERS = 8
STRESS_STORIES = 4
STRESS_SUBTASKS_PER_WORKER = 3
STRESS_PHASES = ("explore", "implement")
STRESS_ATTEMPTS_PER_PHASE = 2


def _record_one_subtask(st: store.Store, story_id: str, card_id: str) -> int:
    """Record one subtask's whole life, each parent before its children.

    Returns how many `record_*` calls it made, so the caller can check the
    journal holds exactly that many lines. `PhaseRun.detail` is left unset on
    purpose: the projection has no column for it, so setting it would make the
    rebuilt tree differ from the rows for a reason unrelated to threading.
    """
    calls = 0
    subtask = _subtask(card_id)
    st.record_subtask(story_id, subtask)
    calls += 1
    for name in STRESS_PHASES:
        phase = models.PhaseRun(name=name, kind="agent", status="started")
        st.record_phase(story_id, card_id, phase)
        calls += 1
        for n in range(1, STRESS_ATTEMPTS_PER_PHASE + 1):
            attempt = models.Attempt(n=n, dispatch=_dispatch(card_id, name, n))
            st.record_attempt(story_id, card_id, name, attempt)
            calls += 1
            st.record_attempt(
                story_id,
                card_id,
                name,
                attempt.model_copy(update={"status": "ok", "exit_code": 0}),
            )
            calls += 1
        st.record_phase(story_id, card_id, phase.model_copy(update={"status": "done"}))
        calls += 1
    st.record_subtask(story_id, subtask.model_copy(update={"status": "done"}))
    calls += 1
    return calls


def test_eight_threads_recording_through_one_store_agree_with_the_rebuilt_journal(repo):
    # P2: the threads of one process share one Store. Deterministic (P7): the
    # assertions are on results only -- the journal's numbering and the tree
    # the rows and the journal each produce -- never on timing.
    st = store.Store.open(repo, RUN_ID)
    story_ids = [f"story-{i}" for i in range(STRESS_STORIES)]
    try:
        # A child needs its parent: the run and every story exist before any
        # thread starts.
        st.record_run(_run(repo))
        for story_id in story_ids:
            st.record_story(_story().model_copy(update={"card_id": story_id}))
        setup_calls = 1 + STRESS_STORIES

        start = threading.Barrier(STRESS_WORKERS)
        errors: list[BaseException] = []
        calls = [0] * STRESS_WORKERS

        def work(worker: int) -> None:
            try:
                start.wait(timeout=30)
                for t in range(STRESS_SUBTASKS_PER_WORKER):
                    # Spread each worker's subtasks across the stories, so every
                    # story gets siblings recorded by several threads at once.
                    index = worker * STRESS_SUBTASKS_PER_WORKER + t
                    story_id = story_ids[index % STRESS_STORIES]
                    calls[worker] += _record_one_subtask(st, story_id, f"w{worker}-t{t}")
            except BaseException as error:  # surfaced by the assertion below
                errors.append(error)

        threads = [
            threading.Thread(target=work, args=(worker,))
            for worker in range(STRESS_WORKERS)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=120)

        assert [thread.is_alive() for thread in threads] == [False] * STRESS_WORKERS
        assert errors == []

        per_subtask = (
            1 + len(STRESS_PHASES) * (1 + 2 * STRESS_ATTEMPTS_PER_PHASE + 1) + 1
        )
        total = setup_calls + sum(calls)
        assert total == setup_calls + (
            STRESS_WORKERS * STRESS_SUBTASKS_PER_WORKER * per_subtask
        )
        assert [line.seq for line in st.journal.read()] == list(range(1, total + 1))

        # Read the rows BEFORE rebuilding: the rebuild rewrites them.
        before = st.load_run(RUN_ID)
        assert before is not None
        assert [story.card_id for story in before.stories] == story_ids
        recorded = [subtask for story in before.stories for subtask in story.subtasks]
        assert sorted(subtask.card_id for subtask in recorded) == sorted(
            f"w{worker}-t{t}"
            for worker in range(STRESS_WORKERS)
            for t in range(STRESS_SUBTASKS_PER_WORKER)
        )
        for subtask in recorded:
            assert subtask.status == "done"
            assert [phase.name for phase in subtask.phases] == list(STRESS_PHASES)
            for phase in subtask.phases:
                assert phase.status == "done"
                assert [a.n for a in phase.attempts] == list(
                    range(1, STRESS_ATTEMPTS_PER_PHASE + 1)
                )
                assert all(a.status == "ok" and a.exit_code == 0 for a in phase.attempts)

        # Review Focus 4: a separate reader connection, as `am status` opens,
        # sees exactly what the threads committed.
        reader = store.open_db(repo)
        try:
            assert store.load_run(reader, RUN_ID) == before
        finally:
            reader.close()

        assert st.rebuild_from_journal(RUN_ID) == before
        assert st.load_run(RUN_ID) == before
    finally:
        st.close()


def _record_stopped_run(st: store.Store, repo: Path) -> None:
    """One run stopped cleanly mid-subtask: `stopped` at every level that uses `Status`."""
    st.record_run(models.Run.model_validate({**_run(repo).model_dump(), "status": "stopped"}))
    st.record_story(
        models.StoryRun(
            card_id="8831189b",
            title="Foundations: paths, run store and journal",
            level=0,
            status="stopped",
            tip_branch="m1/task-ef248597",
        )
    )
    st.record_subtask(
        "8831189b",
        models.SubtaskRun(
            card_id="ef248597",
            branch="m1/task-ef248597",
            base_branch="main",
            status="stopped",
            worktree_path=Path("/repo/.claude/worktrees/m1/task-ef248597"),
        ),
    )
    st.record_phase(
        "8831189b",
        "ef248597",
        models.PhaseRun(
            name="implement",
            kind="agent",
            status="stopped",
            started_at=datetime(2026, 9, 23, 10, 13, tzinfo=timezone.utc),
        ),
    )


def _stopped_levels(run: models.Run) -> tuple[str, str, str, str]:
    story = run.stories[0]
    subtask = story.subtasks[0]
    return run.status, story.status, subtask.status, subtask.phases[0].status


def test_a_stopped_run_loads_back_as_stopped_from_the_rows(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        _record_stopped_run(st, repo)
        loaded = st.load_run(RUN_ID)
    finally:
        st.close()

    assert loaded is not None
    assert _stopped_levels(loaded) == ("stopped", "stopped", "stopped", "stopped")


def test_a_stopped_run_survives_a_rebuild_from_the_journal(repo):
    st = store.Store.open(repo, RUN_ID)
    _record_stopped_run(st, repo)
    st.close()

    _truncate_db(repo)
    rebuilt = store.Store.open(repo, RUN_ID)
    try:
        returned = rebuilt.rebuild_from_journal(RUN_ID)
        after = rebuilt.load_run(RUN_ID)
        row = rebuilt.connection.execute(
            "SELECT status FROM subtasks WHERE run_id = ?", (RUN_ID,)
        ).fetchone()
    finally:
        rebuilt.close()

    assert _stopped_levels(returned) == ("stopped", "stopped", "stopped", "stopped")
    assert after == returned
    assert row["status"] == "stopped"


# -- phase detail --------------------------------------------------------------
#
# `PhaseRun.detail` is why a phase failed (architecture cleanup S2). The journal
# always carried it; these pin that the projection does too. Steps tier: real
# temp DB and journal, no harness.

FAILURE_DETAIL = (
    "verify failed: `uv run pytest` exited 1\n"
    "  FAILED tests/test_store.py::test_naïve_path — assert 'ü' == 'u'\n"
    "  3 failed, 212 passed"
)
"""Multi-line and non-ASCII, the way a real failure reason arrives."""

PHASE_COLUMNS = [
    "run_id",
    "story_id",
    "card_id",
    "name",
    "kind",
    "status",
    "started_at",
    "ended_at",
    "position",
    "detail",
]
"""`detail` is last: `ALTER TABLE ... ADD COLUMN` appends, so a fresh and a
migrated database only agree if the schema puts it there too."""


def _record_failed_phase(
    st: store.Store, repo: Path, detail: str | None = FAILURE_DETAIL
) -> None:
    """One subtask with a finished `implement` (no detail) and a failed `verify`."""
    st.record_run(_run(repo))
    st.record_story(_story())
    st.record_subtask("8831189b", _subtask())
    st.record_phase(
        "8831189b",
        "ef248597",
        models.PhaseRun(
            name="implement",
            kind="agent",
            status="done",
            started_at=datetime(2026, 9, 23, 10, 13, tzinfo=timezone.utc),
            ended_at=datetime(2026, 9, 23, 10, 20, tzinfo=timezone.utc),
        ),
    )
    st.record_phase(
        "8831189b",
        "ef248597",
        models.PhaseRun(
            name="verify",
            kind="deterministic",
            status="failed",
            started_at=datetime(2026, 9, 23, 10, 21, tzinfo=timezone.utc),
            ended_at=datetime(2026, 9, 23, 10, 22, tzinfo=timezone.utc),
            detail=detail,
        ),
    )


def _phase_details(run: models.Run | None) -> list[tuple[str, str, str | None]]:
    assert run is not None
    return [
        (phase.name, phase.status, phase.detail)
        for phase in run.stories[0].subtasks[0].phases
    ]


_EXPECTED_DETAILS = [
    ("implement", "done", None),
    ("verify", "failed", FAILURE_DETAIL),
]


def test_a_fresh_phases_table_carries_detail_as_its_last_column(repo):
    conn = store.open_db(repo)
    try:
        columns = [
            row["name"] for row in conn.execute("PRAGMA table_info(phases)").fetchall()
        ]
    finally:
        conn.close()

    assert columns == PHASE_COLUMNS


def test_a_failed_phase_detail_round_trips_through_load_run(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        _record_failed_phase(st, repo)
        via_store = st.load_run(RUN_ID)
        via_connection = store.load_run(st.connection, RUN_ID)
    finally:
        st.close()

    assert _phase_details(via_store) == _EXPECTED_DETAILS
    assert _phase_details(via_connection) == _EXPECTED_DETAILS


def test_a_failed_phase_detail_survives_a_rebuild_from_the_journal(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        _record_failed_phase(st, repo)
    finally:
        st.close()

    _truncate_db(repo)
    rebuilt = store.Store.open(repo, RUN_ID)
    try:
        returned = rebuilt.rebuild_from_journal(RUN_ID)
        after = rebuilt.load_run(RUN_ID)
    finally:
        rebuilt.close()

    assert _phase_details(after) == _EXPECTED_DETAILS
    assert after == returned


def test_re_recording_a_phase_overwrites_its_detail(repo):
    # The upsert must assign the new value, including NULL: a retry that
    # passes must not keep the old failure reason.
    st = store.Store.open(repo, RUN_ID)
    try:
        _record_failed_phase(st, repo, detail="first reason")
        verify = models.PhaseRun(
            name="verify",
            kind="deterministic",
            status="failed",
            detail="second reason",
        )
        st.record_phase("8831189b", "ef248597", verify)
        changed = st.load_run(RUN_ID)
        st.record_phase(
            "8831189b",
            "ef248597",
            verify.model_copy(update={"status": "done", "detail": None}),
        )
        cleared = st.load_run(RUN_ID)
        rows = st.connection.execute(
            "SELECT COUNT(*) FROM phases WHERE run_id = ?", (RUN_ID,)
        ).fetchone()[0]
    finally:
        st.close()

    assert _phase_details(changed) == [
        ("implement", "done", None),
        ("verify", "failed", "second reason"),
    ]
    assert _phase_details(cleared) == [
        ("implement", "done", None),
        ("verify", "done", None),
    ]
    assert rows == 2


_LEGACY_PHASES = """
DROP TABLE phases;
CREATE TABLE phases (
    run_id     TEXT NOT NULL,
    story_id   TEXT NOT NULL,
    card_id    TEXT NOT NULL,
    name       TEXT NOT NULL,
    kind       TEXT NOT NULL,
    status     TEXT NOT NULL,
    started_at TEXT,
    ended_at   TEXT,
    position   INTEGER NOT NULL,
    PRIMARY KEY (run_id, story_id, card_id, name)
);
INSERT INTO phases (run_id, story_id, card_id, name, kind, status,
                    started_at, ended_at, position)
VALUES ('run-2026-09-23-01', '8831189b', 'ef248597', 'verify', 'deterministic',
        'failed', NULL, NULL, 0);
"""
"""The `phases` table exactly as it shipped before `detail`, with one row."""


def test_a_phases_table_from_before_detail_gains_the_column_and_rebuild_fills_it(repo):
    # The journal already carries the reason; only the projection lost it.
    st = store.Store.open(repo, RUN_ID)
    try:
        _record_failed_phase(st, repo)
    finally:
        st.close()

    legacy = store.open_db(repo)
    legacy.executescript(_LEGACY_PHASES)
    legacy.commit()
    legacy.close()

    migrated = store.open_db(repo)
    try:
        columns = [
            row["name"]
            for row in migrated.execute("PRAGMA table_info(phases)").fetchall()
        ]
        kept = [
            (row["name"], row["status"], row["detail"])
            for row in migrated.execute("SELECT name, status, detail FROM phases")
        ]
        stale = store.load_run(migrated, RUN_ID)
    finally:
        migrated.close()

    assert columns == PHASE_COLUMNS
    assert kept == [("verify", "failed", None)]
    assert _phase_details(stale) == [("verify", "failed", None)]

    # Opening an already-migrated database again adds nothing and raises nothing.
    again = store.open_db(repo)
    try:
        reopened_columns = [
            row["name"] for row in again.execute("PRAGMA table_info(phases)").fetchall()
        ]
    finally:
        again.close()
    assert reopened_columns == PHASE_COLUMNS

    rebuilt = store.Store.open(repo, RUN_ID)
    try:
        rebuilt.rebuild_from_journal(RUN_ID)
        after = rebuilt.load_run(RUN_ID)
    finally:
        rebuilt.close()

    assert _phase_details(after) == _EXPECTED_DETAILS


# -- attempts without usage columns ------------------------------------------
#
# Remove-cost-tracking §5.3 items 4-5: a fresh `attempts` table has no
# tokens/cost columns, and a table created before that keeps them, unwritten
# and unread, because migration is additive-only. Unit tier: real temp DB and
# journal, no subprocess.

ATTEMPT_COLUMNS = [
    "run_id",
    "story_id",
    "card_id",
    "phase",
    "n",
    "status",
    "exit_code",
    "duration",
    "prompt_path",
    "result_path",
    "stdout_path",
    "dispatch",
]

LEGACY_ATTEMPT_COLUMNS = [
    *ATTEMPT_COLUMNS[:8],
    "tokens_in",
    "tokens_out",
    "cost",
    *ATTEMPT_COLUMNS[8:],
]
"""The `attempts` columns as they shipped before 2026-10-03, in table order."""


def _attempt_columns(conn: sqlite3.Connection) -> list[str]:
    return [row["name"] for row in conn.execute("PRAGMA table_info(attempts)").fetchall()]


def test_a_fresh_attempts_table_has_no_usage_columns(repo):
    conn = store.open_db(repo)
    try:
        columns = _attempt_columns(conn)
    finally:
        conn.close()

    assert columns == ATTEMPT_COLUMNS
    assert len(columns) == 12


_LEGACY_ATTEMPTS = """
DROP TABLE attempts;
CREATE TABLE attempts (
    run_id       TEXT NOT NULL,
    story_id     TEXT NOT NULL,
    card_id      TEXT NOT NULL,
    phase        TEXT NOT NULL,
    n            INTEGER NOT NULL,
    status       TEXT NOT NULL,
    exit_code    INTEGER,
    duration     REAL,
    tokens_in    INTEGER,
    tokens_out   INTEGER,
    cost         REAL,
    prompt_path  TEXT,
    result_path  TEXT,
    stdout_path  TEXT,
    dispatch     TEXT NOT NULL,
    PRIMARY KEY (run_id, story_id, card_id, phase, n)
);
"""
"""The `attempts` table exactly as it shipped before 2026-10-03, empty."""


def test_an_attempts_table_that_still_has_the_usage_columns_keeps_working(repo):
    legacy = store.open_db(repo)
    legacy.executescript(_LEGACY_ATTEMPTS)
    legacy.commit()
    legacy.close()

    st = store.Store.open(repo, RUN_ID)
    try:
        columns = _attempt_columns(st.connection)
        _record_full_run(st, repo)
        # Re-recording implement attempt 1 takes the ON CONFLICT path.
        st.record_attempt(
            "8831189b",
            "ef248597",
            "implement",
            models.Attempt(
                n=1,
                dispatch=_dispatch(card="ef248597", phase="implement"),
                status="ok",
                exit_code=0,
                duration=7.5,
            ),
        )
        usage = [
            tuple(row)
            for row in st.connection.execute(
                "SELECT tokens_in, tokens_out, cost FROM attempts ORDER BY phase, n"
            ).fetchall()
        ]
        loaded = st.load_run(RUN_ID)
        rebuilt = st.rebuild_from_journal(RUN_ID)
        after = st.load_run(RUN_ID)
    finally:
        st.close()

    assert columns == LEGACY_ATTEMPT_COLUMNS
    assert usage == [(None, None, None), (None, None, None)]
    assert loaded is not None
    implement = loaded.stories[0].subtasks[1].phases[1].attempts[0]
    assert (implement.status, implement.exit_code, implement.duration) == ("ok", 0, 7.5)
    assert rebuilt == loaded
    assert after == loaded


# -- run milestone id ----------------------------------------------------------
#
# `Run.milestone_id` is the full id of the milestone card a run drives
# (architecture cleanup S2). The journal carries it through `record_run`'s
# model dump; these pin that the projection does too. Steps tier: real temp DB
# and journal, no harness.

MILESTONE_ID = "9c44c2fb-0000-4000-8000-000000000000"

RUN_COLUMNS = [
    "id",
    "workflow",
    "repo_dir",
    "base_branch",
    "branch_prefix",
    "status",
    "started_at",
    "config",
    "milestone_id",
]
"""`milestone_id` is last: `ALTER TABLE ... ADD COLUMN` appends, so a fresh and
a migrated database only agree if the schema puts it there too."""


def _run_columns(conn: sqlite3.Connection) -> list[str]:
    return [row["name"] for row in conn.execute("PRAGMA table_info(runs)").fetchall()]


def test_a_fresh_runs_table_carries_milestone_id_as_its_last_column(repo):
    conn = store.open_db(repo)
    try:
        columns = _run_columns(conn)
    finally:
        conn.close()

    assert columns == RUN_COLUMNS


def test_a_runs_milestone_id_round_trips_through_load_run_and_is_overwritten(repo):
    # A resume re-records an existing run row to stamp it, so the upsert's
    # conflict clause must assign the new value, not keep the old NULL.
    st = store.Store.open(repo, RUN_ID)
    try:
        st.record_run(_run(repo))
        unstamped = st.load_run(RUN_ID)
        st.record_run(_run(repo).model_copy(update={"milestone_id": MILESTONE_ID}))
        via_store = st.load_run(RUN_ID)
        via_connection = store.load_run(st.connection, RUN_ID)
        rows = st.connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    finally:
        st.close()

    assert unstamped is not None and unstamped.milestone_id is None
    assert via_store is not None and via_store.milestone_id == MILESTONE_ID
    assert via_connection is not None and via_connection.milestone_id == MILESTONE_ID
    assert rows == 1


_LEGACY_RUNS = """
DROP TABLE runs;
CREATE TABLE runs (
    id            TEXT PRIMARY KEY,
    workflow      TEXT NOT NULL,
    repo_dir      TEXT NOT NULL,
    base_branch   TEXT NOT NULL,
    branch_prefix TEXT NOT NULL,
    status        TEXT NOT NULL,
    started_at    TEXT,
    config        TEXT NOT NULL
);
INSERT INTO runs (id, workflow, repo_dir, base_branch, branch_prefix, status,
                  started_at, config)
VALUES ('run-2026-09-23-01', 'milestone', '/repo', 'main', 'm1/', 'escalated',
        NULL, '{}');
"""
"""The `runs` table exactly as it shipped before `milestone_id`, with one row."""


def test_a_runs_table_from_before_milestone_id_gains_the_column_and_keeps_its_row(repo):
    fresh = store.open_db(repo)
    try:
        fresh_columns = _run_columns(fresh)
        fresh.executescript(_LEGACY_RUNS)
        fresh.commit()
    finally:
        fresh.close()

    migrated = store.open_db(repo)
    try:
        migrated_columns = _run_columns(migrated)
        kept = [
            (row["id"], row["status"], row["milestone_id"])
            for row in migrated.execute("SELECT id, status, milestone_id FROM runs")
        ]
        old = store.load_run(migrated, RUN_ID)
    finally:
        migrated.close()

    assert migrated_columns == fresh_columns == RUN_COLUMNS
    assert kept == [(RUN_ID, "escalated", None)]
    assert old is not None
    assert (old.id, old.status, old.milestone_id) == (RUN_ID, "escalated", None)

    # Opening an already-migrated database again adds nothing and raises nothing.
    again = store.open_db(repo)
    try:
        reopened_columns = _run_columns(again)
    finally:
        again.close()
    assert reopened_columns == RUN_COLUMNS


def _migrated_legacy(repo: Path, extra_sql: str = "") -> list[store.RunSummary]:
    """`_LEGACY_RUNS` (plus `extra_sql`) written into a fresh database, which a
    second `open_db` then migrates; the migrated projection's listing."""
    fresh = store.open_db(repo)
    try:
        fresh.executescript(_LEGACY_RUNS + extra_sql)
        fresh.commit()
    finally:
        fresh.close()

    migrated = store.open_db(repo)
    try:
        return store.list_runs(migrated)
    finally:
        migrated.close()


def test_an_old_runs_row_lists_with_no_milestone_id_and_no_card_id(repo):
    [summary] = _migrated_legacy(repo)

    assert (summary.id, summary.workflow, summary.status) == (
        RUN_ID,
        "milestone",
        "escalated",
    )
    assert summary.milestone_id is None
    assert summary.card_id is None


def test_an_old_task_run_lists_its_card_id_after_the_milestone_id_migration(repo):
    [summary] = _migrated_legacy(
        repo,
        """
        UPDATE runs SET workflow = 'task' WHERE id = 'run-2026-09-23-01';
        INSERT INTO subtasks (run_id, story_id, card_id, branch, base_branch,
                              status, worktree_path, position)
        VALUES ('run-2026-09-23-01', '8831189b', 'ef248597', 'm1/task-ef248597',
                'main', 'escalated', NULL, 0);
        """,
    )

    assert summary.workflow == "task"
    assert summary.milestone_id is None
    assert summary.card_id == "ef248597"


def test_an_old_runs_row_lists_with_zero_progress(repo):
    """A database from before `milestone_id` has a bare `runs` row and empty
    tree tables; it lists with the zero `progress`, not `null` or an error."""
    [summary] = _migrated_legacy(repo)

    assert summary.progress is not None
    assert summary.progress.model_dump() == _expected()


def test_a_runs_milestone_id_survives_a_rebuild_from_the_journal(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        st.record_run(_run(repo).model_copy(update={"milestone_id": MILESTONE_ID}))
    finally:
        st.close()

    _truncate_db(repo)
    rebuilt = store.Store.open(repo, RUN_ID)
    try:
        returned = rebuilt.rebuild_from_journal(RUN_ID)
        after = rebuilt.load_run(RUN_ID)
    finally:
        rebuilt.close()

    assert returned.milestone_id == MILESTONE_ID
    assert after is not None and after.milestone_id == MILESTONE_ID
    assert after == returned


STORY_ID = "2aeb8b6e-b24f-4d4e-ab81-138f8d7dfbae"


def _with_story(run: models.Run, story_id: str | None) -> models.Run:
    return run.model_copy(
        update={"config": run.config.model_copy(update={"story_id": story_id})}
    )


def test_a_runs_config_story_id_round_trips_through_the_row_and_the_journal(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        st.record_run(_with_story(_run(repo), STORY_ID))
        via_store = st.load_run(RUN_ID)
        via_connection = store.load_run(st.connection, RUN_ID)
        row = st.connection.execute(
            "SELECT config FROM runs WHERE id = ?", (RUN_ID,)
        ).fetchone()
        st.record_run(_with_story(_run(repo), None))
        cleared = st.load_run(RUN_ID)
        rows = st.connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    finally:
        st.close()

    assert via_store is not None and via_store.config.story_id == STORY_ID
    assert via_connection is not None and via_connection.config.story_id == STORY_ID
    assert json.loads(row["config"])["story_id"] == STORY_ID
    assert cleared is not None and cleared.config.story_id is None
    assert rows == 1
    upserts = [line for line in store.Journal(RUN_ID).read() if line.event == "run_upsert"]
    assert [line.payload["config"]["story_id"] for line in upserts] == [STORY_ID, None]
    assert all("story_id" not in line.payload for line in upserts)


def test_a_runs_row_whose_config_has_no_story_id_loads_with_none(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        st.record_run(_run(repo))
        st.connection.execute(
            "UPDATE runs SET config = ? WHERE id = ?",
            (json.dumps({"max_concurrent_stories": 2}), RUN_ID),
        )
        st.connection.commit()
        loaded = st.load_run(RUN_ID)
    finally:
        st.close()

    assert loaded is not None
    assert loaded.config.story_id is None
    assert loaded.config.max_concurrent_stories == 2


def test_a_runs_config_story_id_survives_a_rebuild_from_the_journal(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        st.record_run(_with_story(_run(repo), STORY_ID))
    finally:
        st.close()

    _truncate_db(repo)
    rebuilt = store.Store.open(repo, RUN_ID)
    try:
        returned = rebuilt.rebuild_from_journal(RUN_ID)
        after = rebuilt.load_run(RUN_ID)
    finally:
        rebuilt.close()

    assert returned.config.story_id == STORY_ID
    assert after is not None and after.config.story_id == STORY_ID
    assert after == returned


def test_a_run_upsert_line_without_story_id_rebuilds_to_none(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        st.record_run(_run(repo))
    finally:
        st.close()

    journal_path = store.Journal(RUN_ID).path
    records = [json.loads(text) for text in journal_path.read_text().splitlines()]
    upserts = [record for record in records if record["event"] == "run_upsert"]
    assert upserts
    for record in upserts:
        del record["payload"]["config"]["story_id"]
    journal_path.write_text("".join(json.dumps(record) + "\n" for record in records))

    _truncate_db(repo)
    rebuilt = store.Store.open(repo, RUN_ID)
    try:
        returned = rebuilt.rebuild_from_journal(RUN_ID)
    finally:
        rebuilt.close()

    assert returned.config.story_id is None


RUN_UPSERT_KEYS = {
    "id",
    "workflow",
    "repo_dir",
    "base_branch",
    "branch_prefix",
    "status",
    "started_at",
    "config",
    "milestone_id",
}
"""A `run_upsert` payload: the `Run` dump without `stories`."""

STORY_UPSERT_KEYS = {"card_id", "title", "level", "status", "tip_branch"}
"""A `story_upsert` payload: the `StoryRun` dump without `subtasks`. No milestone key."""


def test_a_milestone_runs_journal_names_its_milestone_once_at_the_head(repo):
    """Card a7fcc076, the "no new journal key" decision, pinned.

    A `--board` run gives each milestone its own Run, so one journal covers
    exactly one milestone. Its first line (lowest `seq`) is the `run_upsert`
    carrying `milestone_id`, and every later line shares that line's
    `run_id`. A `story_upsert` therefore needs no milestone key of its own:
    a consumer reading one run's journal learns the milestone from the head.
    The synthetic ids (`integrate`, `bases`, `base-<story id>`) are recorded
    the same way, under the same `run_id`. Should this ever fail because the
    head is not a `run_upsert` with `milestone_id`, or because lines mix run
    ids, the spec's fallback (an additive `milestone_id` on `story_upsert`)
    applies.
    """
    from agent_manager import bases, integration

    story = _story()
    merged = models.StoryRun(
        card_id=bases.BASES_STORY_ID,
        title=bases.BASES_STORY_TITLE,
        level=0,
        status="started",
    )
    resolver = _subtask(bases.resolver_card_id(story.card_id))
    integrate = models.StoryRun(
        card_id=integration.INTEGRATE_STORY_ID, title="Integrate", level=1, status="started"
    )
    run = _run(repo).model_copy(update={"milestone_id": MILESTONE_ID})
    st = store.Store.open(repo, RUN_ID)
    try:
        st.record_run(run)
        st.record_story(story)
        st.record_story(merged)
        st.record_subtask(bases.BASES_STORY_ID, resolver)
        st.record_story(integrate)
        st.record_run(run.model_copy(update={"status": "done"}))
    finally:
        st.close()

    lines = store.Journal(RUN_ID).read()

    assert [line.event for line in lines] == [
        "run_upsert",
        "story_upsert",
        "story_upsert",
        "subtask_upsert",
        "story_upsert",
        "run_upsert",
    ]
    head = min(lines, key=lambda line: line.seq)
    assert head is lines[0]
    assert head.event == "run_upsert"
    assert set(head.payload) == RUN_UPSERT_KEYS
    assert head.payload["milestone_id"] == MILESTONE_ID
    assert {line.run_id for line in lines} == {RUN_ID}
    for line in lines:
        if line.event == "run_upsert":
            assert line.payload["milestone_id"] == MILESTONE_ID
    story_lines = [line for line in lines if line.event == "story_upsert"]
    assert [line.story for line in story_lines] == [
        story.card_id,
        bases.BASES_STORY_ID,
        integration.INTEGRATE_STORY_ID,
    ]
    for line in story_lines:
        assert set(line.payload) == STORY_UPSERT_KEYS
        assert line.payload["card_id"] == line.story
        assert line.card is None
    (subtask_line,) = [line for line in lines if line.event == "subtask_upsert"]
    assert (subtask_line.story, subtask_line.card) == (
        bases.BASES_STORY_ID,
        f"base-{story.card_id}",
    )
    assert "milestone_id" not in subtask_line.payload


def test_a_run_upsert_line_from_before_milestone_id_rebuilds_to_none(repo):
    # A journal written before the field existed has no `milestone_id` key at
    # all (not `null`): it must still validate, and project a NULL column.
    payload = _run(repo).model_dump(mode="json", exclude={"stories", "milestone_id"})
    assert "milestone_id" not in payload
    st = store.Store.open(repo, RUN_ID)
    try:
        _append_raw(
            st.journal,
            {
                "seq": 1,
                "ts": "2026-09-23T10:00:00+00:00",
                "run_id": RUN_ID,
                "event": "run_upsert",
                "payload": payload,
            },
        )
        returned = st.rebuild_from_journal(RUN_ID)
        after = st.load_run(RUN_ID)
        row = st.connection.execute(
            "SELECT milestone_id FROM runs WHERE id = ?", (RUN_ID,)
        ).fetchone()
    finally:
        st.close()

    assert returned.milestone_id is None
    assert after is not None and after.milestone_id is None
    assert row["milestone_id"] is None


# -- checkpoints ---------------------------------------------------------------
#
# A row-only table outside the journal (pygents-engine spec §6). Steps tier:
# real temp DB and journal, no harness, not engine-parametrised.

OTHER_RUN_ID = "run-2026-09-24-01"


def _at(minute: int) -> datetime:
    return datetime(2026, 9, 25, 12, minute, tzinfo=timezone.utc)


def _save_checkpoint(
    st: store.Store,
    card_id: str = "ef248597",
    *,
    reason: str = "turn",
    workflow: str = "task",
    digest: str = "sha256:aaa",
    agent: dict | None = None,
    saved_at: datetime | None = None,
) -> store.Checkpoint:
    return st.save_checkpoint(
        card_id,
        workflow=workflow,
        digest=digest,
        reason=reason,
        agent={"turn": 0} if agent is None else agent,
        saved_at=_at(0) if saved_at is None else saved_at,
    )


def test_open_db_creates_the_checkpoints_table(repo):
    conn = store.open_db(repo)
    try:
        columns = [
            row["name"]
            for row in conn.execute("PRAGMA table_info(checkpoints)").fetchall()
        ]
    finally:
        conn.close()
    assert columns == [
        "run_id",
        "card_id",
        "seq",
        "workflow",
        "digest",
        "reason",
        "agent",
        "saved_at",
    ]


def test_save_checkpoint_numbers_each_cards_rows_from_zero(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        seqs = [_save_checkpoint(st, "card-a", saved_at=_at(i)).seq for i in range(3)]
        other = _save_checkpoint(st, "card-b", saved_at=_at(3))
        rows = st.connection.execute(
            "SELECT run_id, card_id, seq FROM checkpoints ORDER BY card_id, seq"
        ).fetchall()
    finally:
        st.close()

    assert seqs == [0, 1, 2]
    assert other.seq == 0
    assert other.run_id == RUN_ID
    assert [tuple(row) for row in rows] == [
        (RUN_ID, "card-a", 0),
        (RUN_ID, "card-a", 1),
        (RUN_ID, "card-a", 2),
        (RUN_ID, "card-b", 0),
    ]


def test_latest_checkpoint_is_the_highest_seq_of_this_stores_run(repo):
    other = store.Store.open(repo, OTHER_RUN_ID)
    try:
        for i in range(5):
            _save_checkpoint(other, "card-a", digest="sha256:other", saved_at=_at(i))
    finally:
        other.close()

    st = store.Store.open(repo, RUN_ID)
    try:
        _save_checkpoint(st, "card-a", reason="turn", saved_at=_at(10))
        newest = _save_checkpoint(st, "card-a", reason="parked", saved_at=_at(11))
        latest = st.latest_checkpoint("card-a")
        unknown = st.latest_checkpoint("card-never-saved")
    finally:
        st.close()

    assert latest == newest
    assert latest is not None
    assert latest.run_id == RUN_ID
    assert latest.seq == 1
    assert latest.reason == "parked"
    assert latest.digest == "sha256:aaa"
    assert latest.saved_at == _at(11)
    assert unknown is None


def test_latest_turn_checkpoint_is_the_newest_turn_row_of_this_stores_run(repo):
    """Card 54e4ec29: a phase escalation's newest row holds no turn, so a
    milestone resume rewinds to the turn the failing phase ran in."""
    other = store.Store.open(repo, OTHER_RUN_ID)
    try:
        _save_checkpoint(other, "card-a", reason="turn", saved_at=_at(20))
    finally:
        other.close()

    st = store.Store.open(repo, RUN_ID)
    try:
        _save_checkpoint(st, "card-a", reason="turn", saved_at=_at(0))
        wanted = _save_checkpoint(st, "card-a", reason="turn", agent={"turn": 1}, saved_at=_at(1))
        _save_checkpoint(st, "card-a", reason="escalated", saved_at=_at(2))
        _save_checkpoint(st, "card-b", reason="parked", saved_at=_at(3))
        found = st.latest_turn_checkpoint("card-a")
        only_parked = st.latest_turn_checkpoint("card-b")
        unknown = st.latest_turn_checkpoint("card-never-saved")
    finally:
        st.close()

    assert found == wanted
    assert found is not None and found.run_id == RUN_ID and found.seq == 1
    assert only_parked is None
    assert unknown is None


def test_a_checkpoint_with_an_unknown_reason_is_refused_and_writes_nothing(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            _save_checkpoint(st, "card-a", reason="bogus")

        assert _held_elsewhere(st._lock) is False
        assert st.connection.in_transaction is False
        assert st.connection.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0] == 0
        # The refused save spent no seq.
        assert _save_checkpoint(st, "card-a").seq == 0
    finally:
        st.close()


def test_a_checkpoint_whose_agent_is_not_json_is_refused_and_writes_nothing(repo):
    # Review Focus 4: the agent dict is encoded before anything is written.
    st = store.Store.open(repo, RUN_ID)
    try:
        with pytest.raises(TypeError):
            _save_checkpoint(st, "card-a", agent={"when": _at(0)})

        assert _held_elsewhere(st._lock) is False
        assert st.connection.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0] == 0
        assert _save_checkpoint(st, "card-a").seq == 0
    finally:
        st.close()


def test_a_checkpoints_agent_round_trips_byte_equal(repo):
    agent = {
        "zeta": [3, {"b": 2, "a": 1}],
        "alpha": {"nested": {"y": None, "x": "é"}},
        "count": 7,
    }
    st = store.Store.open(repo, RUN_ID)
    try:
        saved = _save_checkpoint(st, "card-a", agent=agent)
        stored = st.connection.execute(
            "SELECT agent FROM checkpoints WHERE run_id = ? AND card_id = ?",
            (RUN_ID, "card-a"),
        ).fetchone()["agent"]
        read = st.latest_checkpoint("card-a")
    finally:
        st.close()

    assert stored == json.dumps(agent, sort_keys=True)
    assert json.dumps(saved.agent, sort_keys=True) == stored
    assert read is not None
    assert json.dumps(read.agent, sort_keys=True) == stored
    assert read.agent == agent
    assert read == saved

    # Review Focus 5: the returned value is not aliased to the caller's dict.
    agent["count"] = 8
    assert saved.agent["count"] == 7

    with pytest.raises(dataclasses.FrozenInstanceError):
        saved.seq = 9  # type: ignore[misc]


def test_checkpoints_stay_out_of_the_journal_and_survive_a_rebuild(repo):
    # G10: checkpoints are not part of the journaled tree.
    st = store.Store.open(repo, RUN_ID)
    try:
        _record_full_run(st, repo)
        before = [line.seq for line in st.journal.read()]
        saved = _save_checkpoint(st, "ef248597", reason="parked")
        after = [line.seq for line in st.journal.read()]
        st.rebuild_from_journal(RUN_ID)
        kept = st.latest_checkpoint("ef248597")
    finally:
        st.close()

    assert after == before
    assert kept == saved
    assert set(get_args(store.EventKind)) == {
        "run_upsert",
        "story_upsert",
        "subtask_upsert",
        "phase_upsert",
        "attempt_upsert",
    }


def test_latest_open_checkpoint_finds_an_older_runs_parked_row(repo):
    old = store.Store.open(repo, OTHER_RUN_ID)
    try:
        _save_checkpoint(old, "card-a", reason="turn", saved_at=_at(0))
        parked = _save_checkpoint(old, "card-a", reason="parked", saved_at=_at(1))
        # A newer, still-open row under another workflow: ignored by the filter.
        _save_checkpoint(old, "card-a", reason="turn", workflow="integrate", saved_at=_at(2))
    finally:
        old.close()

    new = store.Store.open(repo, RUN_ID)
    try:
        found = new.latest_open_checkpoint("card-a", "task")
        own = new.latest_checkpoint("card-a")
        other_workflow = new.latest_open_checkpoint("card-a", "never-ran")
        unknown = new.latest_open_checkpoint("card-never-saved", "task")
    finally:
        new.close()

    assert found == parked
    assert found is not None
    assert found.run_id == OTHER_RUN_ID
    assert found.seq == 1
    assert found.reason == "parked"
    assert own is None
    assert other_workflow is None
    assert unknown is None


def test_latest_open_checkpoint_is_none_when_the_newest_row_is_done(repo):
    old = store.Store.open(repo, OTHER_RUN_ID)
    try:
        _save_checkpoint(old, "card-a", reason="turn", saved_at=_at(0))
        _save_checkpoint(old, "card-a", reason="parked", saved_at=_at(1))
    finally:
        old.close()

    new = store.Store.open(repo, RUN_ID)
    try:
        _save_checkpoint(new, "card-a", reason="done", saved_at=_at(2))
        assert new.latest_open_checkpoint("card-a", "task") is None
    finally:
        new.close()


def test_latest_open_checkpoint_is_none_when_a_done_row_of_another_workflow_is_newest(repo):
    # Review Focus 2: the newest row "in any run and with any workflow" decides.
    old = store.Store.open(repo, OTHER_RUN_ID)
    try:
        _save_checkpoint(old, "card-a", reason="parked", saved_at=_at(0))
    finally:
        old.close()

    new = store.Store.open(repo, RUN_ID)
    try:
        _save_checkpoint(new, "card-a", reason="done", workflow="integrate", saved_at=_at(1))
        assert new.latest_open_checkpoint("card-a", "task") is None
    finally:
        new.close()


def test_latest_open_checkpoint_returns_a_later_runs_row_after_an_earlier_done(repo):
    # Review Focus 1: a card finished in one run and reopened in a later one.
    old = store.Store.open(repo, OTHER_RUN_ID)
    try:
        _save_checkpoint(old, "card-a", reason="done", saved_at=_at(0))
    finally:
        old.close()

    new = store.Store.open(repo, RUN_ID)
    try:
        reopened = _save_checkpoint(new, "card-a", reason="turn", saved_at=_at(1))
        found = new.latest_open_checkpoint("card-a", "task")
    finally:
        new.close()

    assert found == reopened
    assert found is not None
    assert found.run_id == RUN_ID


def test_latest_open_checkpoint_returns_an_escalated_row(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        _save_checkpoint(st, "card-a", reason="turn", saved_at=_at(0))
        escalated = _save_checkpoint(st, "card-a", reason="escalated", saved_at=_at(1))
        assert st.latest_open_checkpoint("card-a", "task") == escalated
    finally:
        st.close()


def test_latest_open_checkpoint_breaks_a_saved_at_tie_with_seq(repo):
    # Review Focus 3: equal timestamps are ordered by seq.
    st = store.Store.open(repo, RUN_ID)
    try:
        _save_checkpoint(st, "card-a", reason="turn", saved_at=_at(5))
        parked = _save_checkpoint(st, "card-a", reason="parked", saved_at=_at(5))
        assert st.latest_open_checkpoint("card-a", "task") == parked

        _save_checkpoint(st, "card-b", reason="parked", saved_at=_at(5))
        _save_checkpoint(st, "card-b", reason="done", saved_at=_at(5))
        assert st.latest_open_checkpoint("card-b", "task") is None
    finally:
        st.close()


def test_latest_open_checkpoint_skips_a_done_row_of_its_workflow_when_another_is_newest(
    repo,
):
    # The newest row overall is open (another workflow), so the card is not
    # closed; the answer is then the newest *open* row of `workflow`, never its
    # newer `done` row.
    st = store.Store.open(repo, RUN_ID)
    try:
        opened = _save_checkpoint(st, "card-a", reason="turn", saved_at=_at(0))
        _save_checkpoint(st, "card-a", reason="done", saved_at=_at(1))
        _save_checkpoint(st, "card-a", reason="turn", workflow="integrate", saved_at=_at(2))
        assert st.latest_open_checkpoint("card-a", "task") == opened
    finally:
        st.close()


def test_checkpoint_cards_lists_this_runs_distinct_pairs_in_order(repo):
    """af52db54: `am reset` reports every `(card_id, workflow)` the run
    checkpointed, whatever the reason, and nothing another run saved."""
    st = store.Store.open(repo, RUN_ID)
    try:
        _save_checkpoint(st, "card-b", reason="turn", saved_at=_at(0))
        _save_checkpoint(st, "card-a", reason="turn", saved_at=_at(1))
        _save_checkpoint(st, "card-a", reason="parked", saved_at=_at(2))
        _save_checkpoint(st, "card-a", reason="done", saved_at=_at(3))
        _save_checkpoint(st, "card-a", reason="turn", workflow="integrate", saved_at=_at(4))
    finally:
        st.close()

    other = store.Store.open(repo, OTHER_RUN_ID)
    try:
        _save_checkpoint(other, "card-c", reason="turn", saved_at=_at(5))
        _save_checkpoint(other, "card-a", reason="parked", workflow="bases", saved_at=_at(6))
        before = other.connection.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0]
        # Asked from a store bound to another run: the argument decides.
        mine = other.checkpoint_cards(RUN_ID)
        theirs = other.checkpoint_cards(OTHER_RUN_ID)
        unknown = other.checkpoint_cards("run-never-saved")
        after = other.connection.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0]
    finally:
        other.close()

    assert mine == [("card-a", "integrate"), ("card-a", "task"), ("card-b", "task")]
    assert theirs == [("card-a", "bases"), ("card-c", "task")]
    assert unknown == []
    assert after == before == 7


# -- run controls and leases -----------------------------------------------------
#
# Row-only tables outside the journal (live-control spec C1/C2), like
# `checkpoints`. A "second process" is a second `store.open_db` connection.
# Steps tier: real temp DB and journal, no harness.


def test_the_control_tables_appear_on_an_existing_database(repo):
    # A pre-M9 database: every table but the two new ones, with a row in it.
    first = store.open_db(repo)
    first.execute("DROP TABLE IF EXISTS run_controls")
    first.execute("DROP TABLE IF EXISTS run_leases")
    first.execute(
        "INSERT INTO runs (id, workflow, repo_dir, base_branch, branch_prefix,"
        " status, started_at, config) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (RUN_ID, "milestone", str(repo), "main", "m1/", "stopped", None, "{}"),
    )
    first.commit()
    first.close()

    conn = store.open_db(repo)
    try:
        names = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        controls = [
            row["name"] for row in conn.execute("PRAGMA table_info(run_controls)").fetchall()
        ]
        leases = [
            row["name"] for row in conn.execute("PRAGMA table_info(run_leases)").fetchall()
        ]
        kept = [row["id"] for row in conn.execute("SELECT id FROM runs").fetchall()]
    finally:
        conn.close()

    assert {"run_controls", "run_leases"} <= names
    assert controls == ["run_id", "seq", "lease", "command", "requested_at", "handled_at"]
    assert leases == [
        "run_id",
        "token",
        "pid",
        "host",
        "acquired_at",
        "heartbeat_at",
        "accepting",
    ]
    assert kept == [RUN_ID]


def test_a_lease_is_touched_only_through_its_own_token(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        lease = st.take_lease(
            token="t1", pid=42, host="h", now=_at(0), is_live=lambda row: False
        ).lease
        assert lease == store.LeaseRow(
            run_id=RUN_ID,
            token="t1",
            pid=42,
            host="h",
            acquired_at=_at(0),
            heartbeat_at=_at(0),
            accepting=True,
        )
        assert store.read_lease(st.connection, RUN_ID) == lease

        st.beat("other", _at(1))
        st.close_window("other")
        st.release_lease("other")
        assert store.read_lease(st.connection, RUN_ID) == lease

        st.beat("t1", _at(1))
        st.close_window("t1")
        row = store.read_lease(st.connection, RUN_ID)
        assert row is not None
        assert row.heartbeat_at == _at(1)
        assert row.acquired_at == _at(0)
        assert row.accepting is False

        st.release_lease("t1")
        assert store.read_lease(st.connection, RUN_ID) is None
        assert store.read_lease(st.connection, "run-never-leased") is None
        assert st.connection.in_transaction is False
    finally:
        st.close()

    with pytest.raises(dataclasses.FrozenInstanceError):
        lease.token = "t2"  # type: ignore[misc]


# -- lease hand-off (am run --detach, card aff9fdbf) ----------------------------


def test_set_lease_holder_moves_pid_and_host_only_for_its_own_token(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        lease = st.take_lease(
            token="t1", pid=42, host="h", now=_at(0), is_live=lambda row: False
        ).lease

        st.set_lease_holder("other", pid=7, host="elsewhere")
        assert store.read_lease(st.connection, RUN_ID) == lease

        st.set_lease_holder("t1", pid=7, host="elsewhere")
        assert store.read_lease(st.connection, RUN_ID) == dataclasses.replace(
            lease, pid=7, host="elsewhere"
        )
        assert st.connection.in_transaction is False
    finally:
        st.close()


def test_adopt_lease_binds_the_held_token_and_numbers_after_the_last_line(repo):
    first = store.Store.open(repo, RUN_ID)
    second = store.Store.open(repo, RUN_ID)
    try:
        first.take_lease(token="t1", pid=1, host="h", now=_at(0), is_live=lambda row: False)
        assert first.record_run(_run(repo)).seq == 1
        first.bind_lease(None)

        held = second.adopt_lease("t1")

        assert held.token == "t1"
        # Opened before line 1 was written: only the reseek numbers this line 2.
        assert second.record_run(_run(repo)).seq == 2
        thief = store.Store.open(repo, RUN_ID)
        try:
            thief.take_lease(token="thief", pid=9, host="h", now=_at(1), is_live=lambda row: False)
        finally:
            thief.close()
        with pytest.raises(store.LeaseLostError):
            second.record_run(_run(repo))
    finally:
        first.close()
        second.close()


def test_adopt_lease_refuses_a_token_that_does_not_hold_the_run(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        with pytest.raises(store.LeaseLostError) as missing:
            st.adopt_lease("t1")
        assert missing.value.holder is None

        st.take_lease(token="t2", pid=1, host="h", now=_at(0), is_live=lambda row: False)
        st.bind_lease(None)
        with pytest.raises(store.LeaseLostError) as other:
            st.adopt_lease("t1")
        assert other.value.holder is not None and other.value.holder.token == "t2"

        # Still unbound: bound to t1, this write would have been fenced out.
        assert st.record_run(_run(repo)).event == "run_upsert"
    finally:
        st.close()


def test_reacquiring_a_lease_replaces_the_old_token_and_other_runs_are_untouched(repo):
    # Review Focus 3: a resumed run takes a new token; the old one is dead, and
    # a token string shared with another run never reaches that run's row.
    other = store.Store.open(repo, OTHER_RUN_ID)
    st = store.Store.open(repo, RUN_ID)
    try:
        theirs = other.take_lease(
            token="new", pid=7, host="h", now=_at(0), is_live=lambda row: False
        ).lease
        st.take_lease(token="old", pid=1, host="h", now=_at(0), is_live=lambda row: False)
        st.close_window("old")
        fresh = st.take_lease(
            token="new", pid=2, host="h", now=_at(5), is_live=lambda row: False
        ).lease

        st.beat("old", _at(9))
        st.close_window("old")
        st.release_lease("old")
        assert store.read_lease(st.connection, RUN_ID) == fresh
        assert fresh.accepting is True
        assert (
            st.connection.execute(
                "SELECT COUNT(*) FROM run_leases WHERE run_id = ?", (RUN_ID,)
            ).fetchone()[0]
            == 1
        )

        st.release_lease("new")
        assert store.read_lease(st.connection, RUN_ID) is None
        assert store.read_lease(st.connection, OTHER_RUN_ID) == theirs
    finally:
        st.close()
        other.close()


def test_a_request_from_another_connection_is_pending_for_its_lease_only(repo):
    st = store.Store.open(repo, RUN_ID)
    other = store.open_db(repo)
    try:
        with store.immediate(other):
            first = store.add_control(
                other, RUN_ID, lease="t1", command="pause", requested_at=_at(0)
            )
            second = store.add_control(
                other, RUN_ID, lease="old", command="cancel", requested_at=_at(0)
            )
        assert first == store.ControlRow(
            run_id=RUN_ID,
            seq=0,
            lease="t1",
            command="pause",
            requested_at=_at(0),
            handled_at=None,
        )
        assert second.seq == 1

        assert [row.command for row in st.pending_controls("t1")] == ["pause"]
        assert st.pending_controls("t1") == [first]

        st.mark_control_handled(0, _at(1))
        assert st.pending_controls("t1") == []
        assert [row.handled_at for row in store.control_requests(other, RUN_ID)] == [
            _at(1),
            None,
        ]
        assert [row.seq for row in store.control_requests(other, RUN_ID, lease="old")] == [1]
        assert store.control_requests(other, "run-never-controlled") == []
    finally:
        other.close()
        st.close()


def test_control_seqs_are_numbered_and_handled_per_run(repo):
    # Review Focus 4.
    conn = store.open_db(repo)
    try:
        with store.immediate(conn):
            a = store.add_control(conn, RUN_ID, lease="t1", command="pause", requested_at=_at(0))
            b = store.add_control(
                conn, OTHER_RUN_ID, lease="t9", command="cancel", requested_at=_at(0)
            )
            c = store.add_control(conn, RUN_ID, lease="t1", command="cancel", requested_at=_at(1))
        assert (a.seq, b.seq, c.seq) == (0, 0, 1)

        st = store.Store.open(repo, RUN_ID)
        try:
            st.mark_control_handled(0, _at(2))
        finally:
            st.close()

        assert [row.handled_at for row in store.control_requests(conn, RUN_ID)] == [_at(2), None]
        assert [row.handled_at for row in store.control_requests(conn, OTHER_RUN_ID)] == [None]
    finally:
        conn.close()


def test_immediate_rolls_back_on_error(repo):
    conn = store.open_db(repo)
    try:
        with pytest.raises(RuntimeError, match="boom"):
            with store.immediate(conn):
                store.add_control(conn, RUN_ID, lease="t1", command="pause", requested_at=_at(0))
                raise RuntimeError("boom")

        assert conn.in_transaction is False
        assert store.control_requests(conn, RUN_ID) == []
        # Nothing was spent: the next request is still seq 0.
        with store.immediate(conn):
            again = store.add_control(
                conn, RUN_ID, lease="t1", command="pause", requested_at=_at(1)
            )
        assert again.seq == 0
        assert len(store.control_requests(conn, RUN_ID)) == 1
    finally:
        conn.close()


def test_an_unknown_command_is_refused_by_the_check(repo):
    conn = store.open_db(repo)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            with store.immediate(conn):
                store.add_control(
                    conn, RUN_ID, lease="t1", command="resume", requested_at=_at(0)
                )
        assert conn.in_transaction is False
        assert store.control_requests(conn, RUN_ID) == []
    finally:
        conn.close()


def test_immediate_commits_an_implicit_transaction_first(repo):
    # Review Focus 1: Python's legacy sqlite3 mode opens an implicit
    # transaction on the first INSERT; `immediate` must not trip over it.
    conn = store.open_db(repo)
    try:
        conn.execute(
            "INSERT INTO runs (id, workflow, repo_dir, base_branch, branch_prefix,"
            " status, started_at, config) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (RUN_ID, "milestone", str(repo), "main", "m9/", "started", None, "{}"),
        )
        assert conn.in_transaction is True
        with store.immediate(conn):
            store.add_control(conn, RUN_ID, lease="t1", command="pause", requested_at=_at(0))
        assert conn.in_transaction is False
    finally:
        conn.close()

    reader = store.open_db(repo)
    try:
        assert reader.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1
        assert [row.command for row in store.control_requests(reader, RUN_ID)] == ["pause"]
    finally:
        reader.close()


def test_immediate_holds_the_write_lock_from_begin(repo):
    # Review Focus 2: BEGIN IMMEDIATE, not a deferred BEGIN, so no second
    # writer can land between reading MAX(seq) and the insert.
    conn = store.open_db(repo)
    blocker = sqlite3.connect(paths.project_db_path(repo), timeout=0)
    try:
        with store.immediate(conn):
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                blocker.execute("BEGIN IMMEDIATE")
        blocker.execute("BEGIN IMMEDIATE")
        blocker.rollback()
    finally:
        blocker.close()
        conn.close()


def _run_with_status(repo: Path, run_id: str, status: str) -> models.Run:
    return models.Run.model_validate({**_run(repo, run_id).model_dump(), "status": status})


def _checkpoint_in_run(
    repo: Path,
    run_id: str,
    status: str,
    card_id: str,
    *,
    reason: str,
    saved_at: datetime,
    workflow: str = "task",
) -> store.Checkpoint:
    """Record `run_id` with `status`, then save one checkpoint of `card_id` under it."""
    st = store.Store.open(repo, run_id)
    try:
        st.record_run(_run_with_status(repo, run_id, status))
        return _save_checkpoint(st, card_id, reason=reason, workflow=workflow, saved_at=saved_at)
    finally:
        st.close()


def test_a_cancelled_runs_checkpoints_are_never_open(repo):
    # Review Focus 4 of the milestone plan.
    _checkpoint_in_run(repo, "run-r1", "stopped", "c1", reason="parked", saved_at=_at(0))
    _checkpoint_in_run(repo, "run-r2", "cancelled", "c1", reason="parked", saved_at=_at(1))
    unrelated = _checkpoint_in_run(
        repo, "run-r3", "stopped", "c2", reason="parked", saved_at=_at(0)
    )

    st = store.Store.open(repo, "run-r4")
    try:
        closed = st.latest_open_checkpoint("c1", "task")
        found = st.latest_open_checkpoint("c2", "task")
    finally:
        st.close()

    assert closed is None
    assert found == unrelated
    assert found is not None and found.run_id == "run-r3"


def test_latest_open_checkpoint_skips_a_cancelled_runs_row_when_it_is_not_newest(repo):
    # Review Focus 5: the newest row is open (another workflow, a live run), so
    # the card is not closed; the cancelled run's parked row is still skipped.
    older = _checkpoint_in_run(repo, "run-r1", "stopped", "c3", reason="turn", saved_at=_at(0))
    _checkpoint_in_run(repo, "run-r2", "cancelled", "c3", reason="parked", saved_at=_at(1))
    _checkpoint_in_run(
        repo, "run-r3", "stopped", "c3", reason="turn", workflow="integrate", saved_at=_at(2)
    )

    st = store.Store.open(repo, "run-r4")
    try:
        found = st.latest_open_checkpoint("c3", "task")
    finally:
        st.close()

    assert found == older


def test_a_cancelled_run_round_trips_through_the_journal_and_the_listing(repo):
    st = store.Store.open(repo, RUN_ID)
    other = store.open_db(repo)
    try:
        st.record_run(_run_with_status(repo, RUN_ID, "cancelled"))
        lease = st.take_lease(
            token="t1", pid=42, host="h", now=_at(0), is_live=lambda row: False
        ).lease
        with store.immediate(other):
            store.add_control(other, RUN_ID, lease="t1", command="cancel", requested_at=_at(1))
        controls = store.control_requests(other, RUN_ID)
        journal_before = [line.event for line in st.journal.read()]

        rebuilt = st.rebuild_from_journal(RUN_ID)

        # Nothing journals the control tables, and the rebuild leaves them alone.
        assert [line.event for line in st.journal.read()] == journal_before
        assert journal_before == ["run_upsert"]
        assert store.read_lease(st.connection, RUN_ID) == lease
        assert store.control_requests(st.connection, RUN_ID) == controls
        assert rebuilt.status == "cancelled"
        assert store.run_status(st.connection, RUN_ID) == "cancelled"
        assert store.run_status(st.connection, "run-never-recorded") is None
    finally:
        other.close()
        st.close()

    # From the journal alone: a wiped projection replays `cancelled`.
    _truncate_db(repo)
    replayed = store.Store.open(repo, RUN_ID)
    try:
        replayed.rebuild_from_journal(RUN_ID)
        summaries = store.list_runs(replayed.connection)
        status = store.run_status(replayed.connection, RUN_ID)
    finally:
        replayed.close()

    assert [(summary.id, summary.status) for summary in summaries] == [(RUN_ID, "cancelled")]
    assert status == "cancelled"


# -- run claims, lease takeover and fencing ----------------------------------------
#
# Multi-process design X4/X5/X9. Steps tier: real temp DB and journal, no
# harness. A "second process" is a second `Store`/connection, except in the
# two-process race test, which uses real child processes ordered by pipes.


def _alive(row: store.LeaseRow) -> bool:
    return True


def _dead(row: store.LeaseRow) -> bool:
    return False


def _plant_lease(repo: Path, run_id: str, *, token: str) -> store.LeaseRow:
    """A `run_leases` row, as another process's `take_lease` would have left it."""
    conn = store.open_db(repo)
    try:
        with store.immediate(conn):
            conn.execute(
                "INSERT INTO run_leases (run_id, token, pid, host, acquired_at,"
                " heartbeat_at, accepting) VALUES (?, ?, 1, 'h', ?, ?, 1)"
                " ON CONFLICT(run_id) DO UPDATE SET token = excluded.token",
                (run_id, token, _at(0).isoformat(), _at(0).isoformat()),
            )
        row = store.read_lease(conn, run_id)
    finally:
        conn.close()
    assert row is not None
    return row


def _plant_claim(repo: Path, key: str, *, run_id: str, token: str) -> None:
    """A `run_claims` row, as another process's `take_lease` would have left it."""
    conn = store.open_db(repo)
    try:
        with store.immediate(conn):
            conn.execute(
                "INSERT INTO run_claims (key, run_id, token, claimed_at)"
                " VALUES (?, ?, ?, ?) ON CONFLICT(key) DO UPDATE SET"
                " run_id = excluded.run_id, token = excluded.token",
                (key, run_id, token, _at(0).isoformat()),
            )
    finally:
        conn.close()


def test_the_claims_table_appears_on_an_existing_database(repo):
    # A pre-M10 database: everything but `run_claims`.
    first = store.open_db(repo)
    first.execute("DROP TABLE IF EXISTS run_claims")
    first.commit()
    first.close()

    conn = store.open_db(repo)
    try:
        claims = [
            row["name"] for row in conn.execute("PRAGMA table_info(run_claims)").fetchall()
        ]
        leases = [
            row["name"] for row in conn.execute("PRAGMA table_info(run_leases)").fetchall()
        ]
    finally:
        conn.close()

    assert claims == ["key", "run_id", "token", "claimed_at"]
    # No existing table gains a column.
    assert leases == [
        "run_id",
        "token",
        "pid",
        "host",
        "acquired_at",
        "heartbeat_at",
        "accepting",
    ]


def test_lease_errors_name_their_holder_and_a_lost_lease_is_not_an_exception():
    holder = store.LeaseRow(
        run_id=RUN_ID,
        token="t1",
        pid=42,
        host="h",
        acquired_at=_at(0),
        heartbeat_at=_at(0),
        accepting=True,
    )
    held = store.LeaseHeldError(holder)
    assert isinstance(held, RuntimeError) and held.holder == holder

    claimed = store.ClaimHeldError("card:x", holder)
    assert isinstance(claimed, RuntimeError)
    assert (claimed.key, claimed.holder) == ("card:x", holder)

    lost = store.LeaseLostError(RUN_ID, None)
    assert (lost.run_id, lost.holder) == (RUN_ID, None)
    assert isinstance(lost, BaseException) and not isinstance(lost, Exception)
    with pytest.raises(store.LeaseLostError):
        try:
            raise store.LeaseLostError(RUN_ID, holder)
        except Exception:  # must not catch it
            pytest.fail("`except Exception` swallowed LeaseLostError")

    take = store.LeaseTake(lease=holder, displaced=None)
    with pytest.raises(dataclasses.FrozenInstanceError):
        take.displaced = holder  # type: ignore[misc]


def test_claim_conflicts_is_read_only_and_ignores_the_runs_own(repo):
    holder = _plant_lease(repo, "run-a", token="ta")
    _plant_claim(repo, "card:x", run_id="run-a", token="ta")
    # run-c's lease has moved on to a new token: its old claim is dead.
    _plant_lease(repo, "run-c", token="tc-new")
    _plant_claim(repo, "card:z", run_id="run-c", token="tc-old")
    # run-d has no lease row at all.
    _plant_claim(repo, "card:w", run_id="run-d", token="td")
    keys = ["card:x", "card:z", "card:w", "card:never"]

    seen: list[store.LeaseRow] = []

    def live(row: store.LeaseRow) -> bool:
        seen.append(row)
        return True

    conn = store.open_db(repo)
    try:
        changes = conn.total_changes
        assert store.claim_conflicts(conn, keys, is_live=live, run_id="run-b") == [
            ("card:x", holder)
        ]
        # Liveness is asked only of a claim whose run's lease still carries its token.
        assert seen == [holder]
        assert store.claim_conflicts(conn, keys, is_live=_alive) == [("card:x", holder)]
        assert store.claim_conflicts(conn, keys, is_live=_alive, run_id="run-a") == []
        assert store.claim_conflicts(conn, keys, is_live=_dead, run_id="run-b") == []
        assert store.claim_conflicts(conn, [], is_live=_alive) == []
        assert conn.total_changes == changes
        assert conn.in_transaction is False
    finally:
        conn.close()


def test_held_claims_lists_one_tokens_keys_in_key_order(repo):
    _plant_claim(repo, "card:b", run_id="run-a", token="ta")
    _plant_claim(repo, "branch:m10/x", run_id="run-a", token="ta")
    _plant_claim(repo, "card:c", run_id="run-a", token="old")
    _plant_claim(repo, "card:d", run_id="run-b", token="ta")

    conn = store.open_db(repo)
    try:
        rows = store.held_claims(conn, "run-a", "ta")
        nobody = store.held_claims(conn, "run-a", "nobody")
    finally:
        conn.close()

    assert rows == [
        store.ClaimRow(key="branch:m10/x", run_id="run-a", token="ta", claimed_at=_at(0)),
        store.ClaimRow(key="card:b", run_id="run-a", token="ta", claimed_at=_at(0)),
    ]
    assert nobody == []
    with pytest.raises(dataclasses.FrozenInstanceError):
        rows[0].token = "other"  # type: ignore[misc]


@pytest.fixture
def stores(repo) -> Iterator[Callable[..., store.Store]]:
    """Open any number of `Store`s on `repo`, each on its own connection; close them all."""
    opened: list[store.Store] = []

    def open_store(run_id: str = RUN_ID) -> store.Store:
        st = store.Store.open(repo, run_id)
        opened.append(st)
        return st

    yield open_store
    for st in opened:
        st.close()


def test_take_lease_refuses_a_live_foreign_lease(stores):
    mine = stores()
    mine.take_lease(token="t1", pid=1, host="h", now=_at(0), is_live=_alive)
    other = stores()

    with pytest.raises(store.LeaseHeldError) as caught:
        other.take_lease(
            token="t2", pid=2, host="h", now=_at(1), is_live=_alive, claims=["card:x"]
        )

    assert caught.value.holder.token == "t1"
    kept = store.read_lease(other.connection, RUN_ID)
    assert kept is not None and kept.token == "t1"
    assert store.held_claims(other.connection, RUN_ID, "t2") == []
    assert other.connection.in_transaction is False


def test_take_lease_takes_over_a_dead_lease(stores):
    first = stores()
    first.take_lease(token="t1", pid=1, host="h", now=_at(0), is_live=_alive)
    first.close_window("t1")
    second = stores()

    took = second.take_lease(token="t2", pid=2, host="h2", now=_at(5), is_live=_dead)

    assert took.displaced is not None and took.displaced.token == "t1"
    assert took.lease == store.LeaseRow(
        run_id=RUN_ID,
        token="t2",
        pid=2,
        host="h2",
        acquired_at=_at(5),
        heartbeat_at=_at(5),
        accepting=True,
    )
    assert store.read_lease(second.connection, RUN_ID) == took.lease
    # A run nobody ever leased has nothing to displace.
    fresh = stores(OTHER_RUN_ID).take_lease(
        token="t3", pid=3, host="h", now=_at(0), is_live=_alive
    )
    assert fresh.displaced is None


def test_claims_are_all_or_nothing(stores):
    a, b = stores("run-a"), stores("run-b")
    a.take_lease(token="ta", pid=1, host="h", now=_at(0), is_live=_alive, claims=["card:x"])

    with pytest.raises(store.ClaimHeldError) as caught:
        b.take_lease(
            token="tb",
            pid=2,
            host="h",
            now=_at(0),
            is_live=_alive,
            claims=["card:y", "card:x"],
        )

    assert caught.value.key == "card:x"
    assert (caught.value.holder.run_id, caught.value.holder.token) == ("run-a", "ta")
    assert store.read_lease(b.connection, "run-b") is None
    assert store.held_claims(b.connection, "run-b", "tb") == []  # card:y rolled back too
    assert [claim.key for claim in store.held_claims(b.connection, "run-a", "ta")] == [
        "card:x"
    ]
    assert b.connection.in_transaction is False


def test_a_claim_under_a_dead_lease_is_overwritten(stores):
    a, b = stores("run-a"), stores("run-b")
    a.take_lease(token="ta", pid=1, host="h", now=_at(0), is_live=_alive, claims=["card:x"])

    took = b.take_lease(
        token="tb", pid=2, host="h", now=_at(3), is_live=_dead, claims=["card:x"]
    )

    assert took.displaced is None
    assert store.held_claims(b.connection, "run-b", "tb") == [
        store.ClaimRow(key="card:x", run_id="run-b", token="tb", claimed_at=_at(3))
    ]
    assert store.held_claims(b.connection, "run-a", "ta") == []
    # run-a's lease row itself is not b's to touch.
    other_lease = store.read_lease(b.connection, "run-a")
    assert other_lease is not None and other_lease.token == "ta"


def test_a_resume_rewrites_its_own_runs_claims(stores):
    keys = ["card:x", "branch:m10/x"]
    first = stores()
    first.take_lease(token="t1", pid=1, host="h", now=_at(0), is_live=_alive, claims=keys)
    second = stores()

    took = second.take_lease(
        token="t2", pid=2, host="h", now=_at(4), is_live=_dead, claims=keys
    )

    assert took.displaced is not None and took.displaced.token == "t1"
    assert [
        (claim.key, claim.token, claim.claimed_at)
        for claim in store.held_claims(second.connection, RUN_ID, "t2")
    ] == [("branch:m10/x", "t2", _at(4)), ("card:x", "t2", _at(4))]
    assert store.held_claims(second.connection, RUN_ID, "t1") == []


def test_release_claims_deletes_only_its_own_tokens_rows(repo, stores):
    a, b = stores("run-a"), stores("run-b")
    a.take_lease(
        token="ta", pid=1, host="h", now=_at(0), is_live=_alive, claims=["card:x", "card:y"]
    )
    b.take_lease(token="tb", pid=2, host="h", now=_at(0), is_live=_alive, claims=["card:z"])
    _plant_claim(repo, "card:q", run_id="run-a", token="stale")

    a.release_claims("nobody")
    a.release_claims("ta")

    conn = a.connection
    assert store.held_claims(conn, "run-a", "ta") == []
    assert [claim.key for claim in store.held_claims(conn, "run-a", "stale")] == ["card:q"]
    assert [claim.key for claim in store.held_claims(conn, "run-b", "tb")] == ["card:z"]
    # The lease itself is `release_lease`'s business.
    kept = store.read_lease(conn, "run-a")
    assert kept is not None and kept.token == "ta"
    assert conn.in_transaction is False


def test_the_new_owner_continues_the_sequence(repo, stores):
    a = stores()
    a.take_lease(token="t1", pid=1, host="h", now=_at(0), is_live=_alive)
    b = stores()  # opened, its seq cached at 0, before a's write
    assert a.record_run(_run(repo)).seq == 1  # a writes seq 1 while still the owner

    b.take_lease(token="t2", pid=2, host="h", now=_at(1), is_live=_dead)

    assert b.record_run(_run_with_status(repo, RUN_ID, "stopped")).seq == 2
    assert [line.seq for line in b.journal.read()] == [1, 2]


_TAKER = """
import sys
from datetime import datetime, timezone
from pathlib import Path

from agent_manager import store

root, run_id, token = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
st = store.Store.open(root, run_id)
try:
    print("ready", flush=True)
    if sys.stdin.readline().strip() != "go":
        raise SystemExit("no go")
    try:
        st.take_lease(
            token=token,
            pid=0,
            host="h",
            now=datetime.now(timezone.utc),
            is_live=lambda row: row.token != "t0",
        )
    except store.LeaseHeldError as error:
        print(type(error).__name__, flush=True)
    else:
        print("took", flush=True)
finally:
    st.close()
"""


def _taker(repo: Path, run_id: str, token: str) -> "subprocess.Popen[str]":
    """A real second process that takes `run_id`'s lease once told "go" on stdin.

    It inherits `XDG_DATA_HOME`/`HOME` from the `repo` fixture's monkeypatched
    environment, so it opens the same projection and journal as this test.
    """
    return subprocess.Popen(
        [sys.executable, "-c", _TAKER, str(repo), run_id, token],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )


@pytest.mark.soak
@pytest.mark.parametrize("attempt", range(20))
def test_two_processes_taking_one_dead_lease_leave_exactly_one_owner(repo, attempt):
    _plant_lease(repo, RUN_ID, token="t0")  # a dead owner: t0 is dead to both children
    tokens = ("ta", "tb")
    children = [_taker(repo, RUN_ID, token) for token in tokens]
    try:
        for child in children:
            assert child.stdout is not None
            assert child.stdout.readline().strip() == "ready"
        for child in children:
            assert child.stdin is not None
            child.stdin.write("go\n")
            child.stdin.flush()
        outcomes = {
            token: child.communicate(timeout=60)[0].strip()
            for token, child in zip(tokens, children)
        }
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.wait()

    assert sorted(outcomes.values()) == ["LeaseHeldError", "took"]
    assert [child.returncode for child in children] == [0, 0]
    winner = next(token for token, outcome in outcomes.items() if outcome == "took")
    conn = store.open_db(repo)
    try:
        lease = store.read_lease(conn, RUN_ID)
    finally:
        conn.close()
    assert lease is not None and lease.token == winner


def _lease_race_marks() -> dict[str, tuple]:
    test = test_two_processes_taking_one_dead_lease_leave_exactly_one_owner
    return {mark.name: mark.args for mark in getattr(test, "pytestmark", [])}


def test_the_lease_race_parametrization_runs_all_twenty_attempts_in_soak():
    # Test-tier spec V9, option A: the two-process lease race is a concurrency
    # stress probe (40 real child spawns for one property), so it lives in the
    # opt-in `soak` tier with all 20 attempts instead of being shrunk into the
    # unit tier's 0.5s budget. Marks are read off the function because a soak
    # mark deselects the race test from the default run but not this guard.
    marks = _lease_race_marks()
    assert set(marks) == {"parametrize", "soak"}
    assert marks["parametrize"] == ("attempt", range(20))


def test_a_taken_over_store_writes_nothing(repo, stores):
    a = stores()
    a.take_lease(token="t1", pid=1, host="h", now=_at(0), is_live=_alive)
    a.record_run(_run(repo))
    a.record_story(_story())
    b = stores()
    b.take_lease(token="t2", pid=2, host="h", now=_at(1), is_live=_dead)
    before = a.journal.path.read_bytes()

    writes = [
        lambda: a.record_run(_run_with_status(repo, RUN_ID, "done")),
        lambda: a.record_story(_story().model_copy(update={"status": "done"})),
        lambda: a.record_subtask("8831189b", _subtask()),
        lambda: a.record_phase(
            "8831189b",
            "ef248597",
            models.PhaseRun(name="explore", kind="agent", status="started"),
        ),
        lambda: a.record_attempt(
            "8831189b",
            "ef248597",
            "explore",
            models.Attempt(n=1, dispatch=_dispatch(phase="explore")),
        ),
        lambda: _save_checkpoint(a, "ef248597", saved_at=_at(2)),
        lambda: a.rebuild_from_journal(RUN_ID),
    ]
    for write in writes:
        with pytest.raises(store.LeaseLostError) as caught:
            write()
        assert caught.value.run_id == RUN_ID
        assert caught.value.holder is not None and caught.value.holder.token == "t2"
        assert a.connection.in_transaction is False

    assert a.journal.path.read_bytes() == before
    # The new owner's projection is exactly what a wrote while it was the owner.
    assert store.run_status(b.connection, RUN_ID) == "started"
    projected = b.load_run(RUN_ID)
    assert projected is not None
    assert [(story.status, story.subtasks) for story in projected.stories] == [("started", [])]
    assert b.latest_checkpoint("ef248597") is None

    # A deleted (not replaced) lease is lost too, and names no holder.
    b.release_lease("t2")
    with pytest.raises(store.LeaseLostError) as caught:
        a.record_run(_run(repo))
    assert caught.value.holder is None
    assert a.journal.path.read_bytes() == before


def test_an_unbound_store_writes_as_before(repo, stores):
    holder = stores()
    holder.take_lease(token="t9", pid=9, host="h", now=_at(0), is_live=_alive)
    st = stores()
    with pytest.raises(store.LeaseHeldError):
        st.take_lease(token="t1", pid=1, host="h", now=_at(0), is_live=_alive)

    # Neither the refused take nor the foreign live lease binds or fences `st`.
    _record_full_run(st, repo)
    _save_checkpoint(st, "ef248597", saved_at=_at(1))
    st.rebuild_from_journal(RUN_ID)
    assert st.connection.in_transaction is False

    reader = store.open_db(repo)
    try:
        assert store.run_status(reader, RUN_ID) == "started"
        projected = store.load_run(reader, RUN_ID)
    finally:
        reader.close()
    assert projected is not None
    assert [subtask.card_id for subtask in projected.stories[0].subtasks] == [
        "fdebc746",
        "ef248597",
    ]
    kept = store.read_lease(st.connection, RUN_ID)
    assert kept is not None and kept.token == "t9"


def test_a_bound_store_commits_each_write_inside_its_fence(repo, stores):
    st = stores()
    st.take_lease(token="t1", pid=1, host="h", now=_at(0), is_live=_alive)

    _record_full_run(st, repo)
    first = _save_checkpoint(st, "ef248597", saved_at=_at(1))
    # A refused write inside the fence rolls back, spends no seq, leaves nothing open.
    with pytest.raises(sqlite3.IntegrityError):
        _save_checkpoint(st, "ef248597", reason="bogus", saved_at=_at(2))
    assert st.connection.in_transaction is False
    second = _save_checkpoint(st, "ef248597", saved_at=_at(3))
    st.rebuild_from_journal(RUN_ID)
    assert st.connection.in_transaction is False

    # Another connection sees every write: each fence committed its own work.
    reader = store.open_db(repo)
    try:
        assert store.run_status(reader, RUN_ID) == "started"
        projected = store.load_run(reader, RUN_ID)
        seqs = [
            row["seq"]
            for row in reader.execute(
                "SELECT seq FROM checkpoints WHERE run_id = ? ORDER BY seq", (RUN_ID,)
            ).fetchall()
        ]
    finally:
        reader.close()
    assert projected is not None
    assert [subtask.card_id for subtask in projected.stories[0].subtasks] == [
        "fdebc746",
        "ef248597",
    ]
    assert (first.seq, second.seq) == (0, 1)
    assert seqs == [0, 1]


def test_a_bound_rebuild_that_fails_midway_leaves_the_projection_whole(repo, stores):
    # The fence makes the delete and every rewrite one transaction: a rewrite
    # the database refuses must not leave the run's rows deleted.
    st = stores()
    st.take_lease(token="t1", pid=1, host="h", now=_at(0), is_live=_alive)
    _record_full_run(st, repo)

    saboteur = store.open_db(repo)
    try:
        saboteur.execute(
            "CREATE TRIGGER refuse_stories BEFORE INSERT ON stories"
            " BEGIN SELECT RAISE(ABORT, 'refused'); END"
        )
        saboteur.commit()
    finally:
        saboteur.close()

    with pytest.raises(sqlite3.IntegrityError):
        st.rebuild_from_journal(RUN_ID)
    assert st.connection.in_transaction is False

    reader = store.open_db(repo)
    try:
        assert store.run_status(reader, RUN_ID) == "started"
        projected = store.load_run(reader, RUN_ID)
    finally:
        reader.close()
    assert projected is not None
    assert [subtask.card_id for subtask in projected.stories[0].subtasks] == [
        "fdebc746",
        "ef248597",
    ]


# -- checkpoint floors -----------------------------------------------------------
#
# Exactly-once Task 1.1: a row-only `checkpoint_floors` table written in the
# same fenced transaction as its `checkpoints` row. Steps tier: real temp DB and
# journal, no harness.

FLOOR = store.TurnFloor(phase="implement", loop=2, source_run=OTHER_RUN_ID, floor=3)


def _count(st: store.Store, table: str) -> int:
    return st.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_open_db_creates_the_checkpoint_floors_table(repo):
    conn = store.open_db(repo)
    try:
        info = conn.execute("PRAGMA table_info(checkpoint_floors)").fetchall()
        checkpoint_columns = [
            row["name"] for row in conn.execute("PRAGMA table_info(checkpoints)").fetchall()
        ]
    finally:
        conn.close()

    assert [row["name"] for row in info] == [
        "run_id",
        "card_id",
        "seq",
        "phase",
        "loop",
        "source_run",
        "floor",
    ]
    assert [row["name"] for row in sorted(info, key=lambda r: r["pk"]) if row["pk"]] == [
        "run_id",
        "card_id",
        "seq",
    ]
    # M9 C1: the existing table is untouched.
    assert checkpoint_columns == [
        "run_id",
        "card_id",
        "seq",
        "workflow",
        "digest",
        "reason",
        "agent",
        "saved_at",
    ]


def test_checkpoint_floors_refuses_a_negative_floor(repo):
    conn = store.open_db(repo)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO checkpoint_floors (run_id, card_id, seq, phase, loop,"
                " source_run, floor) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (RUN_ID, "card-a", 0, "implement", 0, RUN_ID, -1),
            )
        conn.rollback()
    finally:
        conn.close()


def test_turn_floor_is_frozen_and_checkpoint_floor_defaults_to_none():
    assert [f.name for f in dataclasses.fields(store.TurnFloor)] == [
        "phase",
        "loop",
        "source_run",
        "floor",
    ]
    with pytest.raises(dataclasses.FrozenInstanceError):
        FLOOR.floor = 4  # type: ignore[misc]

    assert dataclasses.fields(store.Checkpoint)[-1].name == "floor"
    plain = store.Checkpoint(
        run_id=RUN_ID,
        card_id="card-a",
        seq=0,
        workflow="task",
        digest="sha256:aaa",
        reason="turn",
        agent={},
        saved_at=_at(0),
    )
    assert plain.floor is None


def _save_floored(
    st: store.Store,
    card_id: str = "card-a",
    *,
    floor: store.TurnFloor | None = FLOOR,
    reason: str = "turn",
    saved_at: datetime | None = None,
) -> store.Checkpoint:
    return st.save_checkpoint(
        card_id,
        workflow="task",
        digest="sha256:aaa",
        reason=reason,
        agent={"turn": 0},
        saved_at=_at(0) if saved_at is None else saved_at,
        floor=floor,
    )


def test_save_checkpoint_writes_the_floor_row_with_the_checkpoint(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        saved = _save_floored(st)
        plain = _save_floored(st, floor=None, saved_at=_at(1))
        rows = st.connection.execute(
            "SELECT run_id, card_id, seq, phase, loop, source_run, floor"
            " FROM checkpoint_floors ORDER BY seq"
        ).fetchall()
        journaled = st.journal.path.exists()
    finally:
        st.close()

    assert saved.floor == FLOOR
    assert saved.seq == 0
    assert plain.floor is None
    assert plain.seq == 1
    # Only the floored save wrote a floor row, keyed like its checkpoint row.
    assert [tuple(row) for row in rows] == [
        (RUN_ID, "card-a", 0, "implement", 2, OTHER_RUN_ID, 3)
    ]
    # Row-only: nothing was journaled (no journal file was ever created).
    assert journaled is False


def test_a_zero_floor_is_accepted(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        zero = dataclasses.replace(FLOOR, floor=0)
        saved = _save_floored(st, floor=zero)
        assert _count(st, "checkpoint_floors") == 1
    finally:
        st.close()

    assert saved.floor == zero


def test_a_negative_floor_is_refused_and_writes_nothing(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        kept = _save_floored(st, floor=None)
        with pytest.raises(sqlite3.IntegrityError):
            _save_floored(st, floor=dataclasses.replace(FLOOR, floor=-1), saved_at=_at(1))

        assert _held_elsewhere(st._lock) is False
        assert st.connection.in_transaction is False
        assert _count(st, "checkpoints") == 1
        assert _count(st, "checkpoint_floors") == 0
        assert st.latest_checkpoint("card-a") == kept
        # The refused save spent no seq: the next one takes seq 1.
        assert _save_floored(st, saved_at=_at(2)).seq == 1
    finally:
        st.close()


def test_an_unknown_reason_with_a_floor_writes_no_floor_row(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            _save_floored(st, reason="bogus")

        assert st.connection.in_transaction is False
        assert _count(st, "checkpoints") == 0
        assert _count(st, "checkpoint_floors") == 0
        assert _save_floored(st).seq == 0
    finally:
        st.close()


def test_a_refused_floor_under_a_held_lease_writes_nothing_and_keeps_the_lease(stores):
    st = stores()
    st.take_lease(token="t1", pid=1, host="h", now=_at(0), is_live=_alive)

    with pytest.raises(sqlite3.IntegrityError):
        _save_floored(st, floor=dataclasses.replace(FLOOR, floor=-1))

    assert _held_elsewhere(st._lock) is False
    assert st.connection.in_transaction is False
    assert _count(st, "checkpoints") == 0
    assert _count(st, "checkpoint_floors") == 0
    lease = store.read_lease(st.connection, RUN_ID)
    assert lease is not None and lease.token == "t1"
    saved = _save_floored(st)
    assert saved.seq == 0
    assert saved.floor == FLOOR
    assert _count(st, "checkpoint_floors") == 1


def test_a_taken_over_store_saves_neither_row_with_a_floor(stores):
    a = stores()
    a.take_lease(token="t1", pid=1, host="h", now=_at(0), is_live=_alive)
    b = stores()
    b.take_lease(token="t2", pid=2, host="h", now=_at(1), is_live=_dead)

    with pytest.raises(store.LeaseLostError) as caught:
        _save_floored(a)

    assert caught.value.holder is not None and caught.value.holder.token == "t2"
    assert a.connection.in_transaction is False
    assert _count(b, "checkpoints") == 0
    assert _count(b, "checkpoint_floors") == 0


def test_every_reader_returns_the_saved_floor(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        saved = _save_floored(st)
        latest = st.latest_checkpoint("card-a")
        turn = st.latest_turn_checkpoint("card-a")
        open_ = st.latest_open_checkpoint("card-a", "task")
    finally:
        st.close()

    for read in (latest, turn, open_):
        assert read is not None
        assert read.floor == FLOOR
        assert read == saved


def test_every_reader_returns_no_floor_for_a_floorless_row(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        _save_checkpoint(st, "card-a", reason="turn")
        reads = [
            st.latest_checkpoint("card-a"),
            st.latest_turn_checkpoint("card-a"),
            st.latest_open_checkpoint("card-a", "task"),
        ]
    finally:
        st.close()

    for read in reads:
        assert read is not None
        assert read.floor is None


def test_floored_and_floorless_rows_of_one_card_read_back_per_seq(repo):
    other_floor = store.TurnFloor(phase="review", loop=0, source_run=RUN_ID, floor=0)
    st = store.Store.open(repo, RUN_ID)
    try:
        _save_floored(st, saved_at=_at(0))
        assert st.latest_checkpoint("card-a").floor == FLOOR

        plain = _save_floored(st, floor=None, saved_at=_at(1))
        assert st.latest_checkpoint("card-a").floor is None

        parked = _save_floored(st, floor=other_floor, reason="parked", saved_at=_at(2))
        latest = st.latest_checkpoint("card-a")
        turn = st.latest_turn_checkpoint("card-a")
        open_ = st.latest_open_checkpoint("card-a", "task")
    finally:
        st.close()

    assert latest == parked and latest.floor == other_floor
    assert turn == plain and turn.floor is None
    assert open_ == parked and open_.floor == other_floor


def test_a_floor_never_attaches_to_another_runs_or_cards_row(repo):
    old = store.Store.open(repo, OTHER_RUN_ID)
    try:
        old_saved = _save_floored(old, "card-a", saved_at=_at(0))
    finally:
        old.close()

    st = store.Store.open(repo, RUN_ID)
    try:
        floored_b = _save_floored(st, "card-b", saved_at=_at(0))
        own = _save_floored(st, "card-a", floor=None, saved_at=_at(1))
        latest = st.latest_checkpoint("card-a")
        turn = st.latest_turn_checkpoint("card-a")
        open_ = st.latest_open_checkpoint("card-a", "task")
        b = st.latest_checkpoint("card-b")
    finally:
        st.close()

    # All three rows have seq 0; only the matching (run_id, card_id, seq) joins.
    assert own.seq == old_saved.seq == floored_b.seq == 0
    for read in (latest, turn, open_):
        assert read is not None
        assert read.run_id == RUN_ID and read.card_id == "card-a"
        assert read.floor is None
    assert b is not None and b.floor == FLOOR


def test_latest_open_checkpoint_returns_an_older_runs_floor(repo):
    old = store.Store.open(repo, OTHER_RUN_ID)
    try:
        parked = _save_floored(old, "card-a", reason="parked", saved_at=_at(0))
    finally:
        old.close()

    st = store.Store.open(repo, RUN_ID)
    try:
        found = st.latest_open_checkpoint("card-a", "task")
    finally:
        st.close()

    assert found == parked
    assert found is not None and found.floor == FLOOR


def test_a_rebuild_keeps_checkpoint_floors(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        _record_full_run(st, repo)
        before = [line.seq for line in st.journal.read()]
        saved = _save_floored(st, "ef248597")
        after = [line.seq for line in st.journal.read()]
        st.rebuild_from_journal(RUN_ID)
        kept = st.latest_checkpoint("ef248597")
        floors = _count(st, "checkpoint_floors")
    finally:
        st.close()

    assert after == before
    assert floors == 1
    assert kept == saved
    assert kept is not None and kept.floor == FLOOR


def test_checkpoint_from_row_without_floor_columns_has_no_floor(repo):
    # Regression guard: passes before this task's change and must keep passing
    # once _checkpoint_from_row reads the joined floor_* keys.
    st = store.Store.open(repo, RUN_ID)
    try:
        _save_floored(st)
        row = st.connection.execute(
            "SELECT * FROM checkpoints WHERE run_id = ? AND card_id = ?",
            (RUN_ID, "card-a"),
        ).fetchone()
    finally:
        st.close()

    assert store._checkpoint_from_row(row).floor is None


# -- replaying a journal for adoption (exactly-once Task 2.1) ----------------

ADOPTING_RUN_ID = "run-2"


def test_ignore_torn_tail_skips_only_an_unterminated_last_line(repo):
    journal = store.Journal(RUN_ID)
    journal.append("run_upsert", {"i": 0})
    with journal.path.open("a", encoding="utf-8") as handle:
        handle.write('{"seq": 2, "run_id"')

    with pytest.raises(store.CorruptJournalError):
        journal.read()
    assert [line.payload["i"] for line in journal.read(ignore_torn_tail=True)] == [0]


def test_ignore_torn_tail_still_rejects_a_bad_line_before_the_last(repo):
    journal = store.Journal(RUN_ID)
    journal.append("run_upsert", {"i": 0})
    with journal.path.open("a", encoding="utf-8") as handle:
        handle.write("this is not json\n")
        handle.write('{"seq": 3')

    with pytest.raises(store.CorruptJournalError) as excinfo:
        journal.read(ignore_torn_tail=True)
    assert ":2:" in str(excinfo.value)


def test_ignore_torn_tail_still_raises_for_a_missing_journal(repo):
    journal = store.Journal("run-never-started")
    with pytest.raises(store.MissingJournalError):
        journal.read(ignore_torn_tail=True)


def test_replay_journal_of_another_run_ignores_a_torn_tail(repo):
    other = store.Store.open(repo, ADOPTING_RUN_ID)
    try:
        other.record_run(_run(repo, ADOPTING_RUN_ID))
        other.record_story(_story())
    finally:
        other.close()
    torn = paths.run_dir(ADOPTING_RUN_ID) / store.JOURNAL_NAME
    with torn.open("a", encoding="utf-8") as handle:
        handle.write('{"seq": 9')

    st = store.Store.open(repo, RUN_ID)
    try:
        replayed = st.replay_journal(ADOPTING_RUN_ID)

        # The same bytes, now newline-terminated, are a finished line that is
        # not JSON: that is corruption, not an append in flight.
        with torn.open("a", encoding="utf-8") as handle:
            handle.write("\n")
        with pytest.raises(store.CorruptJournalError) as excinfo:
            st.replay_journal(ADOPTING_RUN_ID)
    finally:
        st.close()

    assert replayed.id == ADOPTING_RUN_ID
    assert [story.card_id for story in replayed.stories] == ["8831189b"]
    assert ":3:" in str(excinfo.value)


def test_replay_journal_of_its_own_run_never_ignores_a_torn_tail(repo):
    # Review Focus 4: only another run, which may be live elsewhere, gets the
    # benefit of the doubt. The own journal is this process's to write.
    st = store.Store.open(repo, RUN_ID)
    try:
        st.record_run(_run(repo))
        with st.journal.path.open("a", encoding="utf-8") as handle:
            handle.write('{"seq": 9')
        with pytest.raises(store.CorruptJournalError):
            st.replay_journal(RUN_ID)
    finally:
        st.close()


def test_replay_journal_of_its_own_run_returns_the_recorded_tree(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        _record_full_run(st, repo)
        replayed = st.replay_journal(RUN_ID)
        loaded = st.load_run(RUN_ID)
    finally:
        st.close()

    assert replayed == loaded


def test_replay_journal_holds_the_store_lock(repo, monkeypatch):
    st = store.Store.open(repo, RUN_ID)
    try:
        st.record_run(_run(repo))
        st.record_story(_story())
        real_read = st.journal.read
        seen: list[bool] = []
        writers: list[threading.Thread] = []

        def interleaving_read(**kwargs):
            # A record_* started from another thread mid-replay must wait for
            # the replay: if it could land now, this read would include it.
            seen.append(_held_elsewhere(st._lock))
            writer = threading.Thread(
                target=st.record_subtask, args=("8831189b", _subtask())
            )
            writers.append(writer)
            writer.start()
            writer.join(timeout=0.2)
            seen.append(writer.is_alive())
            return real_read(**kwargs)

        monkeypatch.setattr(st.journal, "read", interleaving_read)
        replayed = st.replay_journal(RUN_ID)
        writers[0].join()
        after = store.replay(real_read())
    finally:
        st.close()

    assert seen == [True, True]
    assert replayed.stories[0].subtasks == []
    assert [subtask.card_id for subtask in after.stories[0].subtasks] == ["ef248597"]


def test_replay_journal_writes_nothing(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        _record_full_run(st, repo)
        before = st.journal.path.read_bytes()
        st.connection.execute("DELETE FROM attempts")
        st.connection.commit()
        st.replay_journal(RUN_ID)
        attempts = st.connection.execute("SELECT COUNT(*) FROM attempts").fetchone()[0]
        after = st.journal.path.read_bytes()
    finally:
        st.close()

    assert after == before
    assert attempts == 0


# -- reading event kinds this version does not recognise (am-watch §3.4) ------
#
# Default suite, unmarked: real temp JSONL/SQLite files, no git, brd or
# subprocess. A newer `am` may journal an event kind this version's `EventKind`
# does not list; reading skips that line, writing stays strict.

UNRECOGNISED_EVENT = "future_upsert"


def _unrecognised_line(seq: int, run_id: str = RUN_ID, **extra: object) -> dict:
    """A well-formed envelope whose `event` this version's `EventKind` lacks."""
    assert UNRECOGNISED_EVENT not in get_args(store.EventKind)
    record: dict = {
        "seq": seq,
        "ts": "2026-10-02T10:00:00+00:00",
        "run_id": run_id,
        "event": UNRECOGNISED_EVENT,
        "payload": {"anything": "at all"},
    }
    record.update(extra)
    return record


def test_read_skips_a_line_whose_event_it_does_not_recognise(repo):
    journal = store.Journal(RUN_ID)
    journal.append("run_upsert", {"i": 0})
    _append_raw(journal, _unrecognised_line(2))
    _append_raw(
        journal,
        {
            "seq": 3,
            "ts": "2026-10-02T10:01:00+00:00",
            "run_id": RUN_ID,
            "event": "run_upsert",
            "payload": {"i": 2},
        },
    )

    lines = journal.read()

    assert [line.seq for line in lines] == [1, 3]
    assert [line.payload["i"] for line in lines] == [0, 2]
    assert all(line.event == "run_upsert" for line in lines)


def test_an_unrecognised_event_is_skipped_whatever_else_the_line_holds(repo):
    journal = store.Journal(RUN_ID)
    journal.append("run_upsert", {"i": 0})
    _append_raw(
        journal,
        _unrecognised_line(
            2,
            operator="someone",
            ts="not a timestamp",
            story=123,
            attempt="first",
            payload={"nested": [1, {"x": None}], "status": "whatever"},
        ),
    )

    assert [line.seq for line in journal.read()] == [1]


def test_an_unrecognised_event_without_a_valid_seq_still_raises(repo):
    # Review Focus 1: `last_seq` must recover a skipped line's seq, so a line
    # with no usable seq is not tolerated just because its event is unknown.
    journal = store.Journal(RUN_ID)
    journal.append("run_upsert", {"i": 0})
    without_seq = _unrecognised_line(2)
    del without_seq["seq"]
    _append_raw(journal, without_seq)

    with pytest.raises(ValidationError) as excinfo:
        journal.read()
    assert "seq" in str(excinfo.value)

    journal.path.write_text("", encoding="utf-8")
    _append_raw(journal, _unrecognised_line(0))
    with pytest.raises(ValidationError) as excinfo:
        journal.read()
    assert "seq" in str(excinfo.value)


@pytest.mark.parametrize(
    "raw",
    [
        '{"seq": 2, "ts": "2026-10-02T10:00:00+00:00", "run_id": "r", "payload": {}}',
        '{"seq": 2, "ts": "2026-10-02T10:00:00+00:00", "run_id": "r", "event": null, "payload": {}}',
        '{"seq": 2, "ts": "2026-10-02T10:00:00+00:00", "run_id": "r", "event": 5, "payload": {}}',
        '["future_upsert"]',
        "null",
    ],
    ids=["no-event", "null-event", "int-event", "array", "json-null"],
)
def test_a_line_without_a_string_event_or_not_an_object_still_raises(repo, raw):
    # Review Focus 2: only a *string* event outside EventKind is skipped.
    journal = store.Journal(RUN_ID)
    journal.append("run_upsert", {"i": 0})
    with journal.path.open("a", encoding="utf-8") as handle:
        handle.write(raw + "\n")

    with pytest.raises(ValidationError):
        journal.read()


def test_read_passes_unknown_payload_keys_on_a_known_event_through(repo):
    # Review Focus 4: §3.4's "ignore unknown payload keys" holds at the read
    # layer because `payload` is an untyped dict; `replay()` still judges it.
    journal = store.Journal(RUN_ID)
    journal.append("run_upsert", {"i": 0})
    _append_raw(
        journal,
        {
            "seq": 2,
            "ts": "2026-10-02T10:00:00+00:00",
            "run_id": RUN_ID,
            "event": "run_upsert",
            "payload": {"i": 1, "future_key": {"deep": True}},
        },
    )

    assert journal.read()[1].payload == {"i": 1, "future_key": {"deep": True}}


def test_ignore_torn_tail_and_an_unrecognised_event_are_both_skipped(repo):
    # Review Focus 3.
    journal = store.Journal(RUN_ID)
    journal.append("run_upsert", {"i": 0})
    _append_raw(journal, _unrecognised_line(2))
    with journal.path.open("a", encoding="utf-8") as handle:
        handle.write('{"seq": 3, "run_id"')

    assert [line.payload["i"] for line in journal.read(ignore_torn_tail=True)] == [0]
    with pytest.raises(store.CorruptJournalError) as excinfo:
        journal.read()
    assert ":3:" in str(excinfo.value)


def test_append_still_refuses_an_unrecognised_event(repo):
    journal = store.Journal(RUN_ID)
    journal.append("run_upsert", {"i": 0})
    _append_raw(journal, _unrecognised_line(2))
    before = journal.path.read_bytes()

    with pytest.raises(ValidationError):
        journal.append(UNRECOGNISED_EVENT, {})  # type: ignore[arg-type]

    assert journal.path.read_bytes() == before
    assert journal._lock.locked() is False


def test_replay_and_rebuild_of_its_own_run_skip_an_unrecognised_event(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        _record_full_run(st, repo)
        before = st.replay_journal(RUN_ID)
        _append_raw(st.journal, _unrecognised_line(st.journal.last_seq() + 1))
        replayed = st.replay_journal(RUN_ID)
        rebuilt = st.rebuild_from_journal(RUN_ID)
        loaded = st.load_run(RUN_ID)
    finally:
        st.close()

    assert replayed == before
    assert rebuilt == before
    assert loaded == before


def test_replay_journal_of_another_run_skips_an_unrecognised_event(repo):
    other = store.Store.open(repo, ADOPTING_RUN_ID)
    try:
        other.record_run(_run(repo, ADOPTING_RUN_ID))
        other.record_story(_story())
        next_seq = other.journal.last_seq() + 1
    finally:
        other.close()

    st = store.Store.open(repo, RUN_ID)
    try:
        before = st.replay_journal(ADOPTING_RUN_ID)
        path = paths.run_dir(ADOPTING_RUN_ID) / store.JOURNAL_NAME
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(_unrecognised_line(next_seq, ADOPTING_RUN_ID)) + "\n")
            handle.write('{"seq": 99')
        after = st.replay_journal(ADOPTING_RUN_ID)
    finally:
        st.close()

    assert after == before
    assert after.id == ADOPTING_RUN_ID
    assert [story.card_id for story in after.stories] == ["8831189b"]


def test_a_skipped_line_still_counts_toward_last_seq(repo):
    # `append` promises no seq is ever repeated on disk: an older `am` resuming
    # a newer `am`'s run must number its next line above the skipped one.
    first = store.Journal(RUN_ID)
    first.append("run_upsert", {"i": 0})
    _append_raw(first, _unrecognised_line(2))

    reopened = store.Journal(RUN_ID)
    assert reopened.last_seq() == 2
    assert reopened.append("run_upsert", {"i": 1}).seq == 3

    again = store.Journal(RUN_ID)
    assert again.last_seq() == 3
    assert [line.seq for line in again.read()] == [1, 3]


def test_reseek_counts_a_skipped_line(repo):
    # Review Focus 5: a lease take-over re-reads the highest seq on disk.
    journal = store.Journal(RUN_ID)
    journal.append("run_upsert", {"i": 0})
    _append_raw(journal, _unrecognised_line(2))

    journal.reseek()

    assert journal.append("run_upsert", {"i": 1}).seq == 3


def test_a_resumed_store_numbers_its_next_record_after_a_skipped_line(repo):
    # Review Focus 5: `Store.open` builds the journal from `last_seq`.
    st = store.Store.open(repo, RUN_ID)
    try:
        st.record_run(_run(repo))
        _append_raw(st.journal, _unrecognised_line(st.journal.last_seq() + 1))
    finally:
        st.close()

    reopened = store.Store.open(repo, RUN_ID)
    try:
        line = reopened.record_run(_run(repo).model_copy(update={"status": "done"}))
    finally:
        reopened.close()

    assert line.seq == 3
    assert [line.seq for line in store.Journal(RUN_ID).read()] == [1, 3]


# -- board comment outbox ----------------------------------------------------------
#
# Board-comments spec B6/B7/B9: a row-only `board_comments` table outside the
# journal, like `checkpoints` and `run_leases`. Steps tier: real temp DB, no
# harness, no brd. The fake-board flush tests belong to `comments.py`.

_COMMENT_COLUMNS = [
    "run_id",
    "card_id",
    "key",
    "body",
    "state",
    "comment_id",
    "failed_attempts",
    "created_at",
    "posted_at",
]


def test_open_db_creates_the_board_comments_table(repo):
    conn = store.open_db(repo)
    try:
        columns = [
            row["name"]
            for row in conn.execute("PRAGMA table_info(board_comments)").fetchall()
        ]
    finally:
        conn.close()
    assert columns == _COMMENT_COLUMNS


def test_the_board_comments_table_appears_on_an_existing_database(repo):
    # A pre-M12 database: every table but the new one, with a row in it.
    first = store.open_db(repo)
    first.execute("DROP TABLE IF EXISTS board_comments")
    first.execute(
        "INSERT INTO runs (id, workflow, repo_dir, base_branch, branch_prefix,"
        " status, started_at, config) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (RUN_ID, "milestone", str(repo), "main", "m1/", "stopped", None, "{}"),
    )
    first.commit()
    first.close()

    conn = store.open_db(repo)
    try:
        columns = [
            row["name"]
            for row in conn.execute("PRAGMA table_info(board_comments)").fetchall()
        ]
        kept = [row["id"] for row in conn.execute("SELECT id FROM runs").fetchall()]
    finally:
        conn.close()

    assert columns == _COMMENT_COLUMNS
    assert kept == [RUN_ID]


def test_a_board_comment_with_an_unknown_state_is_refused(repo):
    conn = store.open_db(repo)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO board_comments (run_id, card_id, key, body, state,"
                " created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (RUN_ID, "card-a", "k-bogus", "body", "bogus", _at(0).isoformat()),
            )
        conn.rollback()
        count = conn.execute("SELECT COUNT(*) FROM board_comments").fetchone()[0]
    finally:
        conn.close()
    assert count == 0


def _enqueue(
    st: store.Store,
    key: str,
    *,
    card_id: str = "card-a",
    run_id: str = RUN_ID,
    body: str = "body",
    now: datetime | None = None,
) -> bool:
    return st.enqueue_comment(
        run_id=run_id,
        card_id=card_id,
        key=key,
        body=body,
        now=_at(0) if now is None else now,
    )


def _comment_row(conn: sqlite3.Connection, key: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM board_comments WHERE key = ?", (key,)).fetchone()


def test_enqueue_comment_inserts_once_and_never_overwrites(repo):
    body = "## Done\n\nmerged `m12/x` — é\n"
    st = store.Store.open(repo, RUN_ID)
    try:
        first = _enqueue(st, "k1", body=body, now=_at(1))
        again = _enqueue(
            st, "k1", run_id=OTHER_RUN_ID, card_id="card-b", body="other", now=_at(2)
        )
        row = _comment_row(st.connection, "k1")
        count = st.connection.execute("SELECT COUNT(*) FROM board_comments").fetchone()[0]
        journal_exists = st.journal.path.exists()
    finally:
        st.close()

    assert first is True
    assert again is False
    assert count == 1
    assert row is not None
    assert dict(row) == {
        "run_id": RUN_ID,
        "card_id": "card-a",
        "key": "k1",
        "body": body,
        "state": "pending",
        "comment_id": None,
        "failed_attempts": 0,
        "created_at": _at(1).isoformat(),
        "posted_at": None,
    }
    # Row-only: the outbox never appends a journal line (the journal file is
    # only created by its first append).
    assert journal_exists is False


def test_an_enqueue_under_a_lost_lease_raises_and_writes_nothing(stores):
    a = stores()
    a.take_lease(token="t1", pid=1, host="h", now=_at(0), is_live=_alive)
    b = stores()
    b.take_lease(token="t2", pid=2, host="h", now=_at(1), is_live=_dead)

    with pytest.raises(store.LeaseLostError) as caught:
        _enqueue(a, "k1")
    assert caught.value.holder is not None and caught.value.holder.token == "t2"
    assert a.connection.in_transaction is False
    assert _comment_row(b.connection, "k1") is None

    # The store holding the lease writes under its own fence and commits it:
    # `a`, a separate connection, sees the committed row.
    assert _enqueue(b, "k1") is True
    assert b.connection.in_transaction is False
    assert _comment_row(a.connection, "k1") is not None


def test_a_refused_enqueue_rolls_back_and_writes_nothing(repo, stores):
    # Review Focus 4: a database error inside the fence rolls back cleanly.
    st = stores()
    st.take_lease(token="t1", pid=1, host="h", now=_at(0), is_live=_alive)

    saboteur = store.open_db(repo)
    try:
        saboteur.execute(
            "CREATE TRIGGER refuse_comments BEFORE INSERT ON board_comments"
            " BEGIN SELECT RAISE(ABORT, 'refused'); END"
        )
        saboteur.commit()
    finally:
        saboteur.close()

    with pytest.raises(sqlite3.IntegrityError):
        _enqueue(st, "k1")
    assert st.connection.in_transaction is False
    assert _held_elsewhere(st._lock) is False
    assert _comment_row(st.connection, "k1") is None

    st.connection.execute("DROP TRIGGER refuse_comments")
    st.connection.commit()
    assert _enqueue(st, "k1") is True


def test_an_enqueue_with_no_body_is_refused_not_ignored(repo):
    # Review Focus 5: only a key collision is ignored; NOT NULL still raises.
    st = store.Store.open(repo, RUN_ID)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            _enqueue(st, "k1", body=None)  # type: ignore[arg-type]
        assert st.connection.in_transaction is False
        assert _comment_row(st.connection, "k1") is None
        assert _enqueue(st, "k1") is True
    finally:
        st.close()


def test_pending_comments_filters_by_run_and_cards_oldest_first(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        _enqueue(st, "k1", card_id="card-a", run_id=RUN_ID, body="one", now=_at(2))
        _enqueue(st, "k2", card_id="card-b", run_id=RUN_ID, body="two", now=_at(1))
        _enqueue(st, "k3", card_id="card-a", run_id=OTHER_RUN_ID, body="three", now=_at(0))

        def keys(**filters) -> list[str]:
            return [row.key for row in st.pending_comments(**filters)]

        every = st.pending_comments()
        by_run = keys(run_id=RUN_ID)
        by_cards = keys(card_ids=["card-a"])
        both = keys(run_id=RUN_ID, card_ids=["card-a"])
        empty = keys(card_ids=[])
        one_shot = keys(card_ids=iter(["card-b"]))
        unknown = keys(card_ids=("card-never",), run_id=OTHER_RUN_ID)
    finally:
        st.close()

    assert [row.key for row in every] == ["k3", "k2", "k1"]
    assert every[0] == store.CommentRow(
        run_id=OTHER_RUN_ID,
        card_id="card-a",
        key="k3",
        body="three",
        state="pending",
        comment_id=None,
        failed_attempts=0,
    )
    assert by_run == ["k2", "k1"]
    # card_ids reaches across runs: relaunch finds an older run's rows.
    assert by_cards == ["k3", "k1"]
    assert both == ["k1"]
    assert empty == []
    assert one_shot == ["k2"]
    assert unknown == []
    with pytest.raises(dataclasses.FrozenInstanceError):
        every[0].state = "posted"  # type: ignore[misc]


def test_pending_comments_breaks_a_created_at_tie_by_insertion_order(repo):
    # Review Focus 3: one tick composes several bodies with the same `now`.
    st = store.Store.open(repo, RUN_ID)
    try:
        _enqueue(st, "k-b", now=_at(5))
        _enqueue(st, "k-a", now=_at(5))
        _enqueue(st, "k-c", now=_at(5))
        found = [row.key for row in st.pending_comments(run_id=RUN_ID)]
    finally:
        st.close()
    assert found == ["k-b", "k-a", "k-c"]


def test_mark_comment_posted_moves_the_row_out_of_pending(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        _enqueue(st, "k1", now=_at(0))
        _enqueue(st, "k2", now=_at(1))
        st.mark_comment_posted("k1", "c-101", _at(3))
        row = _comment_row(st.connection, "k1")
        pending = [r.key for r in st.pending_comments()]
        in_transaction = st.connection.in_transaction
    finally:
        st.close()

    assert row is not None
    assert (row["state"], row["comment_id"], row["posted_at"]) == (
        "posted",
        "c-101",
        _at(3).isoformat(),
    )
    assert pending == ["k2"]
    assert in_transaction is False


def test_record_comment_failure_abandons_the_row_on_the_third_failure(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        _enqueue(st, "k1")
        counts = []
        states = []
        for _ in range(store.COMMENT_ATTEMPTS):
            counts.append(st.record_comment_failure("k1"))
            row = _comment_row(st.connection, "k1")
            assert row is not None
            states.append(row["state"])
        pending = st.pending_comments()
    finally:
        st.close()

    assert store.COMMENT_ATTEMPTS == 3
    assert counts == [1, 2, 3]
    assert states == ["pending", "pending", "abandoned"]
    assert pending == []


def test_an_unknown_comment_key_is_left_alone(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        st.mark_comment_posted("k-never", "c-1", _at(0))
        failures = st.record_comment_failure("k-never")
        count = st.connection.execute("SELECT COUNT(*) FROM board_comments").fetchone()[0]
        in_transaction = st.connection.in_transaction
    finally:
        st.close()

    assert failures == 0
    assert count == 0
    assert in_transaction is False


def test_a_replayed_enqueue_never_revives_a_posted_or_abandoned_comment(repo):
    # B9: a resumed phase enqueues the same key again after the first run's
    # flush already settled it; the row must not go back to `pending`.
    st = store.Store.open(repo, RUN_ID)
    try:
        _enqueue(st, "k-posted", body="first")
        st.mark_comment_posted("k-posted", "c-101", _at(1))
        _enqueue(st, "k-gone", body="first")
        for _ in range(store.COMMENT_ATTEMPTS):
            st.record_comment_failure("k-gone")

        replays = [
            _enqueue(st, "k-posted", body="again", now=_at(5)),
            _enqueue(st, "k-gone", body="again", now=_at(5)),
        ]
        posted = _comment_row(st.connection, "k-posted")
        gone = _comment_row(st.connection, "k-gone")
        pending = st.pending_comments()
    finally:
        st.close()

    assert replays == [False, False]
    assert posted is not None and gone is not None
    assert (posted["state"], posted["body"], posted["comment_id"]) == (
        "posted",
        "first",
        "c-101",
    )
    assert (gone["state"], gone["body"], gone["failed_attempts"]) == (
        "abandoned",
        "first",
        store.COMMENT_ATTEMPTS,
    )
    assert pending == []


def test_a_failure_on_a_posted_comment_never_abandons_it(repo):
    # Review Focus 1: a late failure must not undo a comment the board has.
    st = store.Store.open(repo, RUN_ID)
    try:
        _enqueue(st, "k1")
        st.mark_comment_posted("k1", "c-101", _at(1))
        for _ in range(store.COMMENT_ATTEMPTS):
            st.record_comment_failure("k1")
        row = _comment_row(st.connection, "k1")
    finally:
        st.close()

    assert row is not None
    assert (row["state"], row["comment_id"]) == ("posted", "c-101")


def test_a_taken_over_store_neither_marks_nor_fails_a_comment(stores):
    # Review Focus 2: every outbox writer is fenced, not only enqueue.
    a = stores()
    a.take_lease(token="t1", pid=1, host="h", now=_at(0), is_live=_alive)
    _enqueue(a, "k1")
    b = stores()
    b.take_lease(token="t2", pid=2, host="h", now=_at(1), is_live=_dead)

    writes = [
        lambda: a.mark_comment_posted("k1", "c-101", _at(2)),
        lambda: a.record_comment_failure("k1"),
    ]
    for write in writes:
        with pytest.raises(store.LeaseLostError) as caught:
            write()
        assert caught.value.holder is not None and caught.value.holder.token == "t2"
        assert a.connection.in_transaction is False

    row = _comment_row(b.connection, "k1")
    assert row is not None
    assert (row["state"], row["comment_id"], row["failed_attempts"], row["posted_at"]) == (
        "pending",
        None,
        0,
        None,
    )

    # The new holder's writes go through its own fence.
    assert b.record_comment_failure("k1") == 1
    b.mark_comment_posted("k1", "c-202", _at(3))
    row = _comment_row(a.connection, "k1")
    assert row is not None
    assert (row["state"], row["comment_id"]) == ("posted", "c-202")


# -- store.diverging (journal/DB divergence §3.2-§3.3) ------------------------
#
# Unit tier: real sqlite and journal files under tmp_path, no subprocess.


def _node(
    story: str | None = None,
    card: str | None = None,
    phase: str | None = None,
    attempt: int | None = None,
) -> dict[str, str | int | None]:
    return {"story": story, "card": card, "phase": phase, "attempt": attempt}


def _raw_sql(repo: Path, sql: str, params: tuple = ()) -> None:
    """Write the projection behind the store's back, as a hand-edit would."""
    conn = sqlite3.connect(paths.project_db_path(repo))
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def _diverging_now(repo: Path) -> list[store.Mismatch]:
    """Load the journal and the projection the way a caller would, and compare."""
    lines = store.Journal(RUN_ID).read()
    conn = store.open_db(repo)
    try:
        projection = store.load_run(conn, RUN_ID)
    finally:
        conn.close()
    assert projection is not None
    return store.diverging(lines, projection)


def test_diverging_finds_nothing_in_a_run_recorded_only_through_the_store(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        _record_full_run(st, repo)
        resumed = _subtask("ef248597", base="m1/task-fdebc746")
        st.record_subtask("8831189b", resumed.model_copy(update={"status": "stopped"}))
        st.record_subtask("8831189b", resumed)  # resumed: re-stamped `started`
        st.record_attempt(
            "8831189b",
            "ef248597",
            "implement",
            models.Attempt(
                n=1,
                dispatch=_dispatch(card="ef248597", phase="implement"),
                status="harness_error",
                exit_code=1,
            ),
        )
        st.record_run(_run(repo).model_copy(update={"status": "escalated"}))
    finally:
        st.close()

    assert _diverging_now(repo) == []


def test_diverging_reports_the_2026_10_03_incident_as_a_foreign_run_status(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        st.record_run(_run(repo))
        st.record_run(_run(repo).model_copy(update={"status": "escalated"}))
    finally:
        st.close()
    _raw_sql(repo, "UPDATE runs SET status = 'cancelled' WHERE id = ?", (RUN_ID,))

    assert _diverging_now(repo) == [
        store.Mismatch(
            node=_node(),
            field="status",
            journal="escalated",
            projection="cancelled",
            kind="foreign",
        )
    ]


def test_diverging_classifies_a_status_set_back_to_an_earlier_journaled_value_stale(repo):
    # Pinned decision (§3.2 known limit): indistinguishable from a crash.
    st = store.Store.open(repo, RUN_ID)
    try:
        st.record_run(_run(repo))
        st.record_story(_story())
        st.record_story(_story().model_copy(update={"status": "done"}))
    finally:
        st.close()
    _raw_sql(repo, "UPDATE stories SET status = 'started' WHERE card_id = '8831189b'")

    assert _diverging_now(repo) == [
        store.Mismatch(
            node=_node(story="8831189b"),
            field="status",
            journal="done",
            projection="started",
            kind="stale",
        )
    ]


def test_diverging_classifies_against_the_nodes_own_history_at_attempt_level(repo):
    # `ok` was journaled for the explore attempt, never for the implement one.
    st = store.Store.open(repo, RUN_ID)
    try:
        _record_full_run(st, repo)
    finally:
        st.close()
    _raw_sql(repo, "UPDATE attempts SET status = 'ok' WHERE phase = 'implement' AND n = 1")

    assert _diverging_now(repo) == [
        store.Mismatch(
            node=_node(story="8831189b", card="ef248597", phase="implement", attempt=1),
            field="status",
            journal="started",
            projection="ok",
            kind="foreign",
        )
    ]


def test_diverging_ignores_every_field_but_status(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        _record_full_run(st, repo)
    finally:
        st.close()
    _raw_sql(repo, "UPDATE runs SET workflow = 'task', base_branch = 'develop'")
    _raw_sql(repo, "UPDATE stories SET title = 'hand-edited', tip_branch = NULL, level = 3")
    _raw_sql(repo, "UPDATE subtasks SET branch = 'elsewhere', worktree_path = NULL")
    _raw_sql(repo, "UPDATE phases SET detail = 'hand-edited', ended_at = NULL")
    _raw_sql(
        repo, "UPDATE attempts SET duration = 9.5, exit_code = 42, stdout_path = '/elsewhere'"
    )

    assert _diverging_now(repo) == []


def test_diverging_lets_replays_errors_through_unchanged(repo):
    projection = _run(repo)
    with pytest.raises(store.JournalError, match="no run_upsert"):
        store.diverging([], projection)

    headless = store.JournalLine(
        seq=1,
        ts=datetime(2026, 10, 3, tzinfo=timezone.utc),
        run_id=RUN_ID,
        event="story_upsert",
        story="8831189b",
        payload=_story().model_dump(mode="json", exclude={"subtasks"}),
    )
    with pytest.raises(store.JournalError, match="no run_upsert preceded it"):
        store.diverging([headless], projection)

    malformed = store.JournalLine(
        seq=1,
        ts=datetime(2026, 10, 3, tzinfo=timezone.utc),
        run_id=RUN_ID,
        event="run_upsert",
        payload={"id": RUN_ID},
    )
    with pytest.raises(ValidationError):
        store.diverging([malformed], projection)


def test_diverging_mutates_neither_its_lines_nor_its_projection(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        _record_full_run(st, repo)
    finally:
        st.close()
    _raw_sql(repo, "UPDATE runs SET status = 'cancelled' WHERE id = ?", (RUN_ID,))

    lines = store.Journal(RUN_ID).read()
    conn = store.open_db(repo)
    try:
        projection = store.load_run(conn, RUN_ID)
    finally:
        conn.close()
    assert projection is not None
    lines_before = [line.model_copy(deep=True) for line in lines]
    projection_before = projection.model_copy(deep=True)

    assert store.diverging(lines, projection) != []
    assert lines == lines_before
    assert projection == projection_before


def test_diverging_reports_a_journal_line_whose_row_never_landed_as_stale_shape(repo):
    # The setup of test_rebuild_picks_up_a_journal_line_whose_row_never_landed:
    # the journal line is appended, the row write fails on the closed connection.
    st = store.Store.open(repo, RUN_ID)
    st.record_run(_run(repo))
    st.close()
    with pytest.raises(sqlite3.Error):
        st.record_story(_story())

    assert _diverging_now(repo) == [
        store.Mismatch(
            node=_node(story="8831189b"),
            field=None,
            journal="started",
            projection=None,
            kind="stale",
        )
    ]


def test_diverging_reports_a_hand_inserted_subtask_as_foreign_shape(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        st.record_run(_run(repo))
        st.record_story(_story())
    finally:
        st.close()
    _raw_sql(
        repo,
        "INSERT INTO subtasks (run_id, story_id, card_id, branch, base_branch,"
        " status, worktree_path, position) VALUES (?, ?, ?, ?, ?, ?, NULL, ?)",
        (RUN_ID, "8831189b", "deadbeef", "m1/task-deadbeef", "main", "done", 0),
    )

    assert _diverging_now(repo) == [
        store.Mismatch(
            node=_node(story="8831189b", card="deadbeef"),
            field=None,
            journal=None,
            projection="done",
            kind="foreign",
        )
    ]


def test_diverging_reports_a_missing_subtree_once_at_its_root(repo):
    # The subtask row goes; its phase and attempt rows are left orphaned, so
    # load_run never reaches them. One mismatch, not one per descendant.
    st = store.Store.open(repo, RUN_ID)
    try:
        _record_full_run(st, repo)
    finally:
        st.close()
    _raw_sql(repo, "DELETE FROM subtasks WHERE card_id = 'ef248597'")

    assert _diverging_now(repo) == [
        store.Mismatch(
            node=_node(story="8831189b", card="ef248597"),
            field=None,
            journal="started",
            projection=None,
            kind="stale",
        )
    ]


def test_diverging_reports_mismatches_in_tree_walk_order(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        st.record_run(_run(repo))
        st.record_story(_story())
        st.record_subtask("8831189b", _subtask())
        st.record_story(
            _story().model_copy(update={"card_id": "c0ffee12", "title": "Second story"})
        )
    finally:
        st.close()
    # Written deepest-last-first and out of tree order on purpose.
    _raw_sql(repo, "UPDATE stories SET status = 'cancelled' WHERE card_id = 'c0ffee12'")
    _raw_sql(repo, "UPDATE subtasks SET status = 'failed' WHERE card_id = 'ef248597'")
    _raw_sql(repo, "UPDATE runs SET status = 'done' WHERE id = ?", (RUN_ID,))
    # Position -1 sorts it first in the projection; only-in-projection
    # siblings still come after every journal sibling.
    _raw_sql(
        repo,
        "INSERT INTO stories (run_id, card_id, title, level, status, tip_branch,"
        " position) VALUES (?, 'feedface', 'Hand-made', 0, 'pending', NULL, -1)",
        (RUN_ID,),
    )

    assert _diverging_now(repo) == [
        store.Mismatch(
            node=_node(), field="status", journal="started", projection="done",
            kind="foreign",
        ),
        store.Mismatch(
            node=_node(story="8831189b", card="ef248597"), field="status",
            journal="started", projection="failed", kind="foreign",
        ),
        store.Mismatch(
            node=_node(story="c0ffee12"), field="status", journal="started",
            projection="cancelled", kind="foreign",
        ),
        store.Mismatch(
            node=_node(story="feedface"), field=None, journal=None,
            projection="pending", kind="foreign",
        ),
    ]


# -- rebuild_from_journal's foreign-value rail (journal/DB divergence §3.6) ---
#
# Unit tier: real sqlite and journal files under tmp_path, no subprocess.


def _all_rows(repo: Path) -> dict[str, list[tuple]]:
    """Every row of every table, in rowid order, read behind the store's back."""
    conn = sqlite3.connect(paths.project_db_path(repo))
    try:
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
                " AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        return {
            table: conn.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
            for table in tables
        }
    finally:
        conn.close()


def _projected_run_status(repo: Path) -> str | None:
    conn = store.open_db(repo)
    try:
        return store.run_status(conn, RUN_ID)
    finally:
        conn.close()


def _hand_cancel_an_escalated_run(repo: Path) -> None:
    """The 2026-10-03 incident: the journal says `escalated`, a hand-edit `cancelled`.

    A full tree and a checkpoint ride along so the no-row-touched snapshot
    covers tree rows and row-only rows alike.
    """
    st = store.Store.open(repo, RUN_ID)
    try:
        _record_full_run(st, repo)
        st.record_run(_run(repo).model_copy(update={"status": "escalated"}))
        _save_checkpoint(st, "ef248597")
    finally:
        st.close()
    _raw_sql(repo, "UPDATE runs SET status = 'cancelled' WHERE id = ?", (RUN_ID,))


def test_rebuild_refuses_a_hand_edited_run_status_and_touches_no_row(repo):
    _hand_cancel_an_escalated_run(repo)
    before = _all_rows(repo)

    st = store.Store.open(repo, RUN_ID)
    try:
        with pytest.raises(store.ProjectionDivergedError) as caught:
            st.rebuild_from_journal(RUN_ID)
        assert _held_elsewhere(st._lock) is False
        assert st.connection.in_transaction is False
    finally:
        st.close()

    error = caught.value
    assert isinstance(error, RuntimeError)
    assert not isinstance(error, store.JournalError)
    assert error.run_id == RUN_ID
    assert error.mismatches == [
        store.Mismatch(
            node=_node(),
            field="status",
            journal="escalated",
            projection="cancelled",
            kind="foreign",
        )
    ]
    message = str(error)
    assert RUN_ID in message
    assert "run status: journal 'escalated', projection 'cancelled'" in message
    assert "force=True" in message
    assert _projected_run_status(repo) == "cancelled"
    assert _all_rows(repo) == before


def test_rebuild_with_force_overwrites_a_hand_edited_run_status(repo):
    _hand_cancel_an_escalated_run(repo)

    st = store.Store.open(repo, RUN_ID)
    try:
        rebuilt = st.rebuild_from_journal(RUN_ID, force=True)
        loaded = st.load_run(RUN_ID)
        kept = st.latest_checkpoint("ef248597")
    finally:
        st.close()

    assert rebuilt.status == "escalated"
    assert loaded == rebuilt
    assert _projected_run_status(repo) == "escalated"
    assert kept is not None  # row-only: the forced rebuild still leaves it alone


def test_rebuild_refuses_a_hand_inserted_subtask_and_touches_no_row(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        st.record_run(_run(repo))
        st.record_story(_story())
    finally:
        st.close()
    _raw_sql(
        repo,
        "INSERT INTO subtasks (run_id, story_id, card_id, branch, base_branch,"
        " status, worktree_path, position) VALUES (?, ?, ?, ?, ?, ?, NULL, ?)",
        (RUN_ID, "8831189b", "deadbeef", "m1/task-deadbeef", "main", "done", 0),
    )
    before = _all_rows(repo)

    st = store.Store.open(repo, RUN_ID)
    try:
        with pytest.raises(store.ProjectionDivergedError) as caught:
            st.rebuild_from_journal(RUN_ID)
    finally:
        st.close()

    assert caught.value.mismatches == [
        store.Mismatch(
            node=_node(story="8831189b", card="deadbeef"),
            field=None,
            journal=None,
            projection="done",
            kind="foreign",
        )
    ]
    assert (
        "story=8831189b card=deadbeef shape: journal None, projection 'done'"
        in str(caught.value)
    )
    assert _all_rows(repo) == before


def test_rebuild_still_repairs_a_status_set_back_to_an_earlier_journaled_value(repo):
    # `stale` (§3.2): the journal recorded `started` for this story, so the
    # projection is merely behind and the rebuild goes ahead without `force`.
    st = store.Store.open(repo, RUN_ID)
    try:
        st.record_run(_run(repo))
        st.record_story(_story())
        st.record_story(_story().model_copy(update={"status": "done"}))
    finally:
        st.close()
    _raw_sql(repo, "UPDATE stories SET status = 'started' WHERE card_id = '8831189b'")

    st = store.Store.open(repo, RUN_ID)
    try:
        rebuilt = st.rebuild_from_journal(RUN_ID)
        loaded = st.load_run(RUN_ID)
    finally:
        st.close()

    assert rebuilt.stories[0].status == "done"
    assert loaded == rebuilt


def test_rebuild_refusal_names_only_the_foreign_mismatches(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        st.record_run(_run(repo))
        st.record_story(_story())
        st.record_story(_story().model_copy(update={"status": "done"}))
    finally:
        st.close()
    _raw_sql(repo, "UPDATE stories SET status = 'started' WHERE card_id = '8831189b'")
    _raw_sql(repo, "UPDATE runs SET status = 'cancelled' WHERE id = ?", (RUN_ID,))
    before = _all_rows(repo)

    st = store.Store.open(repo, RUN_ID)
    try:
        with pytest.raises(store.ProjectionDivergedError) as caught:
            st.rebuild_from_journal(RUN_ID)
    finally:
        st.close()

    assert caught.value.mismatches == [
        store.Mismatch(
            node=_node(),
            field="status",
            journal="started",
            projection="cancelled",
            kind="foreign",
        )
    ]
    assert "story=8831189b" not in str(caught.value)
    assert _all_rows(repo) == before


def test_a_bound_store_refusing_a_rebuild_leaves_no_transaction_open_and_keeps_its_lease(
    repo, stores
):
    st = stores()
    st.take_lease(token="t1", pid=1, host="h", now=_at(0), is_live=_alive)
    st.record_run(_run(repo))
    st.record_run(_run(repo).model_copy(update={"status": "escalated"}))
    _raw_sql(repo, "UPDATE runs SET status = 'cancelled' WHERE id = ?", (RUN_ID,))

    with pytest.raises(store.ProjectionDivergedError):
        st.rebuild_from_journal(RUN_ID)

    assert st.connection.in_transaction is False
    assert _held_elsewhere(st._lock) is False
    kept = store.read_lease(st.connection, RUN_ID)
    assert kept is not None and kept.token == "t1"
    assert store.run_status(st.connection, RUN_ID) == "cancelled"


def test_a_corrupt_journal_raises_before_the_foreign_value_check(repo):
    _hand_cancel_an_escalated_run(repo)

    # Open first: `Store.open` scans the journal (`Journal.__init__` ->
    # `last_seq`), so the line is corrupted afterwards to reach the rebuild.
    st = store.Store.open(repo, RUN_ID)
    try:
        with store.Journal(RUN_ID).path.open("a", encoding="utf-8") as handle:
            handle.write("{not json at all\n")
        before = _all_rows(repo)

        with pytest.raises(store.CorruptJournalError):
            st.rebuild_from_journal(RUN_ID)
        assert st.connection.in_transaction is False
    finally:
        st.close()

    assert _all_rows(repo) == before


def test_rebuild_of_an_unloadable_projection_refuses_and_force_repairs_it(repo):
    # A value the models cannot validate is certainly not one `am` wrote: the
    # projection read raises before anything is deleted. `force` skips the read.
    st = store.Store.open(repo, RUN_ID)
    try:
        st.record_run(_run(repo))
    finally:
        st.close()
    _raw_sql(repo, "UPDATE runs SET status = 'bogus' WHERE id = ?", (RUN_ID,))
    before = _all_rows(repo)

    st = store.Store.open(repo, RUN_ID)
    try:
        with pytest.raises(ValidationError):
            st.rebuild_from_journal(RUN_ID)
        assert _all_rows(repo) == before
        rebuilt = st.rebuild_from_journal(RUN_ID, force=True)
    finally:
        st.close()

    assert rebuilt.status == "started"
    assert _projected_run_status(repo) == "started"


def _tree(root: Path) -> set[str]:
    """Every path under `root`, relative, directories included."""
    return {str(path.relative_to(root)) for path in root.rglob("*")}


def test_open_db_for_reading_without_a_db_creates_nothing_and_reads_empty(
    repo, tmp_path
):
    before = _tree(tmp_path)

    conn = store.open_db_for_reading(repo)
    try:
        assert store.list_runs(conn) == []
        assert store.latest_run_id(conn) is None
        assert store.load_run(conn, RUN_ID) is None
        assert store.read_lease(conn, RUN_ID) is None
        assert store.control_requests(conn, RUN_ID) == []
    finally:
        conn.close()

    assert _tree(tmp_path) == before
    assert not (tmp_path / "data").exists()


def test_open_db_for_reading_an_existing_db_reads_its_rows_and_cannot_write(repo):
    writer = store.Store.open(repo, RUN_ID)
    writer.record_run(_run(repo))
    writer.close()

    conn = store.open_db_for_reading(repo)
    try:
        assert [summary.id for summary in store.list_runs(conn)] == [RUN_ID]
        assert store.load_run(conn, RUN_ID) is not None
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("DELETE FROM runs")
    finally:
        conn.close()


def test_open_db_for_reading_reads_while_a_writer_holds_a_write_transaction(repo):
    writer = store.Store.open(repo, RUN_ID)
    writer.record_run(_run(repo))
    held = store.open_db(repo)
    try:
        held.execute("BEGIN IMMEDIATE")
        held.execute("UPDATE runs SET status = 'done'")

        conn = store.open_db_for_reading(repo)
        try:
            started = time.monotonic()
            loaded = store.load_run(conn, RUN_ID)
            assert time.monotonic() - started < 1.0
            assert loaded is not None and loaded.status == "started"

            held.commit()
            assert store.load_run(conn, RUN_ID).status == "done"
        finally:
            conn.close()
    finally:
        held.close()
        writer.close()


def test_open_db_for_reading_an_older_schema_still_reads_it(repo):
    writer = store.Store.open(repo, RUN_ID)
    writer.record_run(_run(repo))
    writer.close()
    old = sqlite3.connect(paths.project_db_path(repo))
    old.execute("ALTER TABLE runs DROP COLUMN milestone_id")
    old.execute("DROP TABLE board_comments")
    old.commit()
    old.close()

    conn = store.open_db_for_reading(repo)
    try:
        assert [summary.id for summary in store.list_runs(conn)] == [RUN_ID]
        assert store.load_run(conn, RUN_ID).milestone_id is None
    finally:
        conn.close()


def test_reading_a_journal_that_does_not_exist_creates_no_data_dir(repo, tmp_path):
    with pytest.raises(store.MissingJournalError):
        store.Journal._for_reading("run-that-never-was").read()

    assert not (tmp_path / "data").exists()
