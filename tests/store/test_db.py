"""Behaviour of `agent_manager.store.db`: the projection's DDL, opening and
migrating it, and the write-transaction helper.

Real SQLite files under `tmp_path`; nothing spawns a process, so these are
unit tests. The `repo` fixture redirects `XDG_DATA_HOME` and `HOME`.
"""

import ast
import re
import sqlite3
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent_manager import paths, store
from agent_manager.store import db

RUN_ID = "run-2026-09-23-01"

_REPO = Path(__file__).resolve().parents[2]

_DB_NAMES = (
    "open_db",
    "immediate",
    "open_db_for_reading",
    "BUSY_TIMEOUT_SECONDS",
    "_SCHEMA",
    "_ADDED_COLUMNS",
    "_enable_wal",
)


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


def test_db_is_a_leaf_module_of_the_store_package():
    for function in (db.open_db, db.immediate, db.open_db_for_reading):
        assert callable(function)
        assert function.__module__ == "agent_manager.store.db"
    assert db.BUSY_TIMEOUT_SECONDS == 30.0


def test_the_store_package_does_not_re_export_db_names():
    assert [name for name in _DB_NAMES if hasattr(store, name)] == []


def test_iso_is_the_column_encoding_of_a_timestamp():
    assert db.iso.__module__ == "agent_manager.store.db"
    assert db.iso(None) is None
    stamp = datetime(2026, 10, 7, 12, 30, tzinfo=timezone.utc)
    assert db.iso(stamp) == "2026-10-07T12:30:00+00:00"
    # The private copy in the package is gone: one encoding, one home.
    assert not hasattr(store, "_iso")


def test_db_imports_only_the_stdlib_and_paths():
    # The AST, not `sys.modules`: importing `agent_manager.store.db` always runs
    # the package `__init__` first, so `sys.modules` cannot tell them apart.
    tree = ast.parse(Path(db.__file__).read_text())
    outside: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] not in sys.stdlib_module_names:
                    outside.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                outside.append("." * node.level + module)
            elif module == "agent_manager":
                outside.extend(
                    f"agent_manager.{alias.name}"
                    for alias in node.names
                    if alias.name != "paths"
                )
            elif module.split(".")[0] not in sys.stdlib_module_names:
                outside.append(module)
    assert outside == []


def test_the_wal_deadline_is_read_from_db_at_call_time(repo, monkeypatch):
    pauses: list[float] = []

    def no_sleep(seconds: float) -> None:
        pauses.append(seconds)
        raise AssertionError("open_db slept past a deadline of 0")

    monkeypatch.setattr(db, "BUSY_TIMEOUT_SECONDS", 0)
    monkeypatch.setattr(db.time, "sleep", no_sleep)
    holder = _hold_fresh_db_reserved(repo)
    try:
        with pytest.raises(sqlite3.OperationalError, match="database is locked"):
            db.open_db(repo)
    finally:
        holder.execute("ROLLBACK")
        holder.close()

    assert pauses == []


_THROUGH_THE_PACKAGE = re.compile(
    r"store(_module)?\.(open_db|immediate|open_db_for_reading|BUSY_TIMEOUT_SECONDS"
    r"|_enable_wal|_SCHEMA|_ADDED_COLUMNS)\b"
    r"|from agent_manager\.store import [^d].*\b(open_db|immediate)\b"
)


def test_no_caller_reaches_a_db_name_through_the_store_package():
    # The opt-in tiers (e2e_fake, soak, e2e) never run in the default suite,
    # and `--collect-only` does not run test bodies, so a stale call through
    # the package there would only fail when someone runs that tier.
    me = Path(__file__).resolve()
    hits = [
        f"{path.relative_to(_REPO)}:{number}: {line.strip()}"
        for root in (_REPO / "src", _REPO / "tests")
        for path in sorted(root.rglob("*.py"))
        if path.resolve() != me and "__pycache__" not in path.parts
        for number, line in enumerate(path.read_text().splitlines(), start=1)
        if _THROUGH_THE_PACKAGE.search(line)
    ]
    assert hits == []


def test_open_db_creates_the_project_file_in_wal_mode(repo):
    conn = db.open_db(repo)
    try:
        assert paths.project_db_path(repo).exists()
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        conn.close()


def test_open_db_connection_can_be_used_from_another_thread(repo):
    # P2: the threads of one process share one connection, so open_db must not
    # pin it to the thread that opened it. The busy timeout is explicit and
    # WAL mode is kept.
    conn = db.open_db(repo)
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
            db.BUSY_TIMEOUT_SECONDS * 1000
        )
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        conn.close()


def test_open_db_waits_out_a_writer_holding_a_fresh_db_before_wal(repo):
    holder = _hold_fresh_db_reserved(repo)
    opened: list[sqlite3.Connection] = []
    errors: list[BaseException] = []

    def open_it() -> None:
        try:
            opened.append(db.open_db(repo))
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
    monkeypatch.setattr(db, "BUSY_TIMEOUT_SECONDS", 0.3)
    holder = _hold_fresh_db_reserved(repo)
    try:
        started = time.monotonic()
        with pytest.raises(sqlite3.OperationalError, match="database is locked"):
            db.open_db(repo)
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
        db.open_db(repo)

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
    monkeypatch.setattr(db.time, "sleep", pauses.append)
    monkeypatch.setattr(db, "BUSY_TIMEOUT_SECONDS", 2.0)

    reader = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        with pytest.raises(sqlite3.OperationalError, match="readonly database"):
            db._enable_wal(reader)
    finally:
        reader.close()

    assert pauses == []


def test_open_db_does_not_sleep_when_nothing_holds_a_lock(repo, monkeypatch):
    # Behavior 6: uncontended, the pragma runs once and open_db never pauses.
    pauses: list[float] = []
    monkeypatch.setattr(db.time, "sleep", pauses.append)

    conn = db.open_db(repo)
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        conn.close()

    assert pauses == []


def test_open_db_creates_every_projection_table(repo):
    conn = db.open_db(repo)
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
    first = db.open_db(repo)
    first.execute(
        "INSERT INTO runs (id, workflow, repo_dir, base_branch, branch_prefix,"
        " status, started_at, config) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (RUN_ID, "milestone", str(repo), "main", "m1/", "started", None, "{}"),
    )
    first.commit()
    first.close()

    second = db.open_db(repo)
    try:
        rows = second.execute("SELECT id, workflow FROM runs").fetchall()
    finally:
        second.close()
    assert [(row["id"], row["workflow"]) for row in rows] == [(RUN_ID, "milestone")]


def test_immediate_holds_the_write_lock_from_begin(repo):
    # Review Focus 2: BEGIN IMMEDIATE, not a deferred BEGIN, so no second
    # writer can land between reading MAX(seq) and the insert.
    conn = db.open_db(repo)
    blocker = sqlite3.connect(paths.project_db_path(repo), timeout=0)
    try:
        with db.immediate(conn):
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                blocker.execute("BEGIN IMMEDIATE")
        blocker.execute("BEGIN IMMEDIATE")
        blocker.rollback()
    finally:
        blocker.close()
        conn.close()
