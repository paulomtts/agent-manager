"""`agent_manager.store.legacy`: reading a per-project database from an older
`am` through a private copy. Stdlib `sqlite3` on files under tmp_path; no
process is spawned, so these are unit tests.
"""

import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path

import pytest
from legacyhelpers import (
    LEGACY_TABLES,
    STAMP,
    full_rows,
    lease_row,
    run_row,
    tree,
    write_db,
    write_wal_db,
)

from agent_manager.store import leases as store_leases
from agent_manager.store import legacy as store_legacy


@pytest.fixture
def scratch(monkeypatch, tmp_path) -> Path:
    """Where `tempfile` makes directories for the duration of the test."""
    directory = tmp_path / "scratch"
    directory.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(directory))
    return directory


def test_read_legacy_reads_every_table_the_runs_and_the_leases(tmp_path, scratch):
    repo = tmp_path / "alpha"
    rows = full_rows("run-a", repo)
    rows["runs"].append(run_row("run-0", repo))
    path = write_db(tmp_path / "legacy" / "x.db", rows)

    found = store_legacy.read_legacy(path)

    assert found.path == path
    assert sorted(found.tables) == list(LEGACY_TABLES)
    runs = found.tables["runs"]
    assert runs.columns[:3] == ("id", "workflow", "repo_dir")
    assert len(runs.rows) == 2
    assert found.repo_dirs == (str(repo.resolve()),)
    assert found.run_ids == ("run-0", "run-a")
    assert found.leases == (
        store_leases.LeaseRow(
            run_id="run-a",
            token="tok-run-a",
            pid=4242,
            host="here",
            acquired_at=datetime.fromisoformat(STAMP),
            heartbeat_at=datetime.fromisoformat(STAMP),
            accepting=True,
        ),
    )
    assert list(scratch.iterdir()) == []


def test_read_legacy_without_runs_or_leases_tables_reads_none(tmp_path, scratch):
    path = write_db(tmp_path / "legacy" / "x.db", schema="CREATE TABLE notes (x);")

    found = store_legacy.read_legacy(path)

    assert sorted(found.tables) == ["notes"]
    assert found.repo_dirs == ()
    assert found.run_ids == ()
    assert found.leases == ()


def test_read_legacy_reads_a_lease_in_a_file_with_no_runs(tmp_path, scratch):
    now = datetime.fromisoformat(STAMP)
    path = write_db(
        tmp_path / "legacy" / "x.db", {"run_leases": [lease_row("run-z", heartbeat_at=now)]}
    )

    found = store_legacy.read_legacy(path)

    assert found.run_ids == ()
    assert [lease.run_id for lease in found.leases] == ["run-z"]


def test_read_legacy_reads_a_zero_byte_file_as_empty(tmp_path, scratch):
    path = tmp_path / "legacy" / "x.db"
    path.parent.mkdir()
    path.write_bytes(b"")

    found = store_legacy.read_legacy(path)

    assert found.tables == {}
    assert found.run_ids == ()
    assert path.read_bytes() == b""


def test_read_legacy_refuses_a_file_sqlite_cannot_read(tmp_path, scratch):
    path = tmp_path / "legacy" / "x.db"
    path.parent.mkdir()
    path.write_bytes(b"not a database " * 200)

    with pytest.raises(store_legacy.LegacyUnreadableError) as raised:
        store_legacy.read_legacy(path)

    assert raised.value.path == path
    assert str(path) in str(raised.value)
    assert isinstance(raised.value.__cause__, sqlite3.DatabaseError)
    assert list(scratch.iterdir()) == []


def test_read_legacy_reads_wal_rows_and_leaves_every_file_as_it_was(
    tmp_path, scratch, monkeypatch
):
    repo = tmp_path / "alpha"
    directory = tmp_path / "legacy"
    path = write_wal_db(
        directory / "x.db",
        tmp_path / "build",
        {"runs": [run_row("run-in-wal", repo)]},
        rows={"runs": [run_row("run-a", repo)]},
    )
    (directory / "x.events.lock").write_bytes(b"")
    before = tree(directory)
    assert {"x.db", "x.db-wal", "x.db-shm"} <= set(before)
    opened: list[str] = []
    real_connect = sqlite3.connect

    def spy(database, *args, **kwargs):
        opened.append(str(database))
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", spy)

    found = store_legacy.read_legacy(path)

    assert found.run_ids == ("run-a", "run-in-wal")
    assert tree(directory) == before
    assert opened and not [entry for entry in opened if str(directory) in entry]
    assert list(scratch.iterdir()) == []
