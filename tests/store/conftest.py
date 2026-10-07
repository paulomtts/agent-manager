"""Fixtures shared by the tests of the `agent_manager.store` package."""

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from agent_manager.store import db as store_db


@pytest.fixture
def repo(monkeypatch, tmp_path) -> Path:
    """A redirected data dir plus a stand-in for the project worktree."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = tmp_path / "repo"
    project.mkdir()
    return project


@pytest.fixture
def conns(repo) -> Iterator[tuple[sqlite3.Connection, sqlite3.Connection]]:
    """Two connections on `repo`'s projection: one a leaf writes on, and an
    observer that sees only what the first has committed.

    Both are opened up front, so the observer's schema pass never waits on
    the writer's open transaction.
    """
    conn = store_db.open_db(repo)
    other = store_db.open_db(repo)
    try:
        yield conn, other
    finally:
        conn.close()
        other.close()
