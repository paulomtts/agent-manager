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
