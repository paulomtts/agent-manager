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
