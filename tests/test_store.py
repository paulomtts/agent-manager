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


def test_two_writers_on_one_run_never_reuse_a_sequence_number(repo):
    # Review Focus 3: the sequence is derived from what is on disk at append
    # time, not cached at construction, so a second writer cannot collide.
    first = store.Journal(RUN_ID)
    second = store.Journal(RUN_ID)
    seqs = [
        first.append("run_upsert", {"w": "a"}).seq,
        second.append("run_upsert", {"w": "b"}).seq,
        first.append("run_upsert", {"w": "a"}).seq,
    ]
    assert seqs == [1, 2, 3]
    assert len({line.seq for line in first.read()}) == 3


def test_reading_a_journal_that_does_not_exist_raises(repo):
    journal = store.Journal("run-never-started")
    with pytest.raises(store.MissingJournalError) as excinfo:
        journal.read()
    assert "run-never-started" in str(excinfo.value)
    assert str(journal.path) in str(excinfo.value)


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
