"""`store.backup.backup`: an online copy of `am.db` through SQLite's backup API.

Everything runs in-process on the per-test data directory with stdlib
`sqlite3` and one writer thread at most; nothing spawns a process, so these
are unit tests.
"""

import shutil
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest
from legacyhelpers import project_file, write_db

from agent_manager import paths
from agent_manager.store import backup as store_backup
from agent_manager.store import db as store_db
from agent_manager.store import events as store_events

NOW = datetime(2026, 10, 7, 9, 0, tzinfo=timezone.utc)
TS = "2026-10-07T12:00:00+00:00"


def _append(conn: sqlite3.Connection, project_id: int, count: int) -> None:
    """Commit `count` events on `conn`, one transaction each."""
    for _ in range(count):
        with store_db.immediate(conn):
            store_events.insert(
                conn,
                project_id=project_id,
                run_id="run-a",
                ts=TS,
                kind="phase_started",
                payload={"n": 1},
                source="live",
            )


def _events(location: Path) -> list[store_events.EventRow]:
    """Every `events` row of the database file `location`, read on a fresh connection."""
    conn = store_db.open_reader(location)
    try:
        return store_events.read(conn)
    finally:
        conn.close()


def _integrity(location: Path) -> str:
    conn = sqlite3.connect(location)
    try:
        return conn.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        conn.close()


def _listing(directory: Path) -> list[str]:
    return sorted(entry.name for entry in directory.iterdir())


@pytest.fixture
def out_dir(tmp_path) -> Path:
    directory = tmp_path / "out"
    directory.mkdir()
    return directory


def test_backup_copies_every_event_and_reports_path_and_size(conns, project_id, out_dir):
    conn, _ = conns
    _append(conn, project_id, 5)
    out = out_dir / "copy.db"

    result = store_backup.backup(out, now=NOW)

    assert result == store_backup.BackupResult(path=out, size_bytes=out.stat().st_size)
    assert _listing(out_dir) == ["copy.db"]
    assert _events(out) == _events(paths.db_path())
    assert len(_events(out)) == 5
    assert _integrity(out) == "ok"


def test_backup_under_a_concurrent_writer_is_a_consistent_prefix(
    conns, project_id, out_dir
):
    conn, observer = conns
    started = threading.Event()
    stop = threading.Event()
    errors: list[BaseException] = []

    def write() -> None:
        try:
            for index in range(200):
                if stop.is_set():
                    return
                _append(conn, project_id, 1)
                if index == 19:
                    started.set()
        except BaseException as error:  # noqa: BLE001 - reported by the test
            errors.append(error)
        finally:
            started.set()

    writer = threading.Thread(target=write)
    writer.start()
    try:
        assert started.wait(timeout=10)
        h0 = store_events.head(observer)
        out = out_dir / "copy.db"
        store_backup.backup(out, now=NOW)
        h1 = store_events.head(observer)
    finally:
        stop.set()
        writer.join(timeout=10)

    assert errors == []
    assert _integrity(out) == "ok"
    copied = _events(out)
    k = copied[-1].seq
    assert h0 <= k <= h1
    assert copied == [row for row in _events(paths.db_path()) if row.seq <= k]


def test_backup_contains_rows_still_in_the_wal(conns, project_id, tmp_path, out_dir):
    conn, _ = conns
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.execute("PRAGMA wal_autocheckpoint=0")
    _append(conn, project_id, 3)
    source = paths.db_path()
    assert Path(f"{source}-wal").stat().st_size > 0
    naive = tmp_path / "naive.db"
    shutil.copyfile(source, naive)
    assert _events(naive) == []
    out = out_dir / "copy.db"

    store_backup.backup(out, now=NOW)

    assert [row.seq for row in _events(out)] == [1, 2, 3]


def test_backup_of_a_settled_database(conns, project_id, out_dir):
    conn, observer = conns
    _append(conn, project_id, 4)
    expected = _events(paths.db_path())
    conn.close()
    observer.close()
    source = paths.db_path()
    assert not Path(f"{source}-wal").exists()
    assert not Path(f"{source}-shm").exists()
    out = out_dir / "copy.db"

    store_backup.backup(out, now=NOW)

    assert _listing(out_dir) == ["copy.db"]
    assert _integrity(out) == "ok"
    assert _events(out) == expected


def test_backup_that_fails_while_copying_leaves_no_temporary_file(
    conns, project_id, out_dir, monkeypatch
):
    def failing_open_reader(location: Path) -> sqlite3.Connection:
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(store_db, "open_reader", failing_open_reader)

    with pytest.raises(sqlite3.OperationalError, match="disk I/O error"):
        store_backup.backup(out_dir / "copy.db", now=NOW)

    assert _listing(out_dir) == []


def test_backup_resolves_a_relative_target_against_the_current_directory(
    conns, project_id, out_dir, monkeypatch
):
    monkeypatch.chdir(out_dir)

    result = store_backup.backup(Path("copy.db"), now=NOW)

    assert result.path == out_dir / "copy.db"
    assert result.path.is_absolute()
    assert _integrity(out_dir / "copy.db") == "ok"


def test_default_target_is_stamped_under_backups(conns, project_id):
    result = store_backup.backup(None, now=NOW)

    expected = paths.data_path() / "backups" / "am-20261007T090000Z.db"
    assert result.path == expected
    assert expected.is_file()
    assert _listing(expected.parent) == ["am-20261007T090000Z.db"]


def test_backup_does_not_refuse_an_unmigrated_machine(tmp_path, out_dir):
    write_db(project_file(tmp_path / "repo"))
    source = paths.db_path()
    raw = sqlite3.connect(source)
    try:
        raw.execute("CREATE TABLE kept (value TEXT)")
        raw.execute("INSERT INTO kept VALUES ('before migrate')")
        raw.commit()
    finally:
        raw.close()
    assert paths.legacy_project_dbs()
    out = out_dir / "copy.db"

    store_backup.backup(out, now=NOW)

    copy = sqlite3.connect(out)
    try:
        assert copy.execute("SELECT value FROM kept").fetchall() == [("before migrate",)]
    finally:
        copy.close()
