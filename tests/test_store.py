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

import dataclasses
import json
import sqlite3
import subprocess
import sys
import threading
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
            models.Attempt(n=1, dispatch=_dispatch(), status="ok", exit_code=0, cost=0.42),
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
        assert attempt_row["cost"] == pytest.approx(0.42)
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
            tokens_in=8000,
            tokens_out=1500,
            cost=0.31,
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
    assert finished.cost == pytest.approx(0.31)
    assert finished.stdout_path == Path(f"/runs/{RUN_ID}/ef248597/explore.1/stdout.log")
    assert finished.dispatch.role == "coder"

    in_flight = subtask.phases[1].attempts[0]
    assert in_flight.status == "started"
    assert in_flight.exit_code is None
    assert in_flight.cost is None


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
    assert attempt.tokens_in is None
    assert attempt.tokens_out is None
    assert attempt.cost is None
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


def _record_summary(root: Path, run_id: str, started_at: datetime | None) -> None:
    """One run row in `root`'s projection, with nothing below it."""
    opened = store.Store.open(root, run_id)
    try:
        opened.record_run(_run(root, run_id).model_copy(update={"started_at": started_at}))
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
    assert seen == [
        ("read", True),
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
