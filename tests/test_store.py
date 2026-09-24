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

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

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
