"""Behaviour of `agent_manager.store.db`: the machine database's DDL, opening and
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
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent_manager import paths, store
from agent_manager.store import db
from agent_manager.store import events as store_events
from agent_manager.store import leases as store_leases
from agent_manager.store import projects as store_projects
from agent_manager.store.writer import Store

RUN_ID = "run-2026-09-23-01"

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)

_PROJECT_TABLES = (
    "runs",
    "stories",
    "subtasks",
    "phases",
    "attempts",
    "checkpoints",
    "checkpoint_floors",
    "run_controls",
    "run_leases",
    "run_claims",
    "board_comments",
)
"""Every table whose rows belong to one project."""

_STAMP = "2026-10-07T12:00:00+00:00"

_MINIMAL_ROWS: dict[str, dict[str, object]] = {
    "runs": {
        "id": RUN_ID,
        "workflow": "milestone",
        "repo_dir": "/repo",
        "base_branch": "main",
        "branch_prefix": "m1/",
        "status": "started",
        "config": "{}",
    },
    "stories": {
        "run_id": RUN_ID,
        "card_id": "c1",
        "title": "t",
        "level": 0,
        "status": "pending",
        "position": 0,
    },
    "subtasks": {
        "run_id": RUN_ID,
        "story_id": "s1",
        "card_id": "c1",
        "branch": "b",
        "base_branch": "main",
        "status": "pending",
        "position": 0,
    },
    "phases": {
        "run_id": RUN_ID,
        "story_id": "s1",
        "card_id": "c1",
        "name": "spec",
        "kind": "agent",
        "status": "pending",
        "position": 0,
    },
    "attempts": {
        "run_id": RUN_ID,
        "story_id": "s1",
        "card_id": "c1",
        "phase": "spec",
        "n": 1,
        "status": "started",
        "dispatch": "{}",
    },
    "checkpoints": {
        "run_id": RUN_ID,
        "card_id": "c1",
        "seq": 0,
        "workflow": "task",
        "digest": "d",
        "reason": "turn",
        "agent": "{}",
        "saved_at": _STAMP,
    },
    "checkpoint_floors": {
        "run_id": RUN_ID,
        "card_id": "c1",
        "seq": 0,
        "phase": "spec",
        "loop": 0,
        "source_run": RUN_ID,
        "floor": 0,
    },
    "run_controls": {
        "run_id": RUN_ID,
        "seq": 0,
        "lease": "t1",
        "command": "pause",
        "requested_at": _STAMP,
    },
    "run_leases": {
        "run_id": RUN_ID,
        "token": "t1",
        "pid": 1,
        "host": "h",
        "acquired_at": _STAMP,
        "heartbeat_at": _STAMP,
        "accepting": 1,
    },
    "run_claims": {
        "key": "card:c1",
        "run_id": RUN_ID,
        "token": "t1",
        "claimed_at": _STAMP,
    },
    "board_comments": {
        "run_id": RUN_ID,
        "card_id": "c1",
        "key": "k1",
        "body": "b",
        "state": "pending",
        "created_at": _STAMP,
    },
}
"""One row each table accepts: every NOT NULL column but `project_id` filled."""


def _insert(conn: sqlite3.Connection, table: str, row: dict[str, object]) -> None:
    columns = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    conn.execute(f"INSERT INTO {table} ({columns}) VALUES ({marks})", tuple(row.values()))

_REPO = Path(__file__).resolve().parents[2]

_DB_NAMES = (
    "open_db",
    "immediate",
    "read_snapshot",
    "open_db_for_reading",
    "BUSY_TIMEOUT_SECONDS",
    "_SCHEMA",
    "_ADDED_COLUMNS",
    "_enable_wal",
    "SCHEMA_VERSION",
    "MIGRATED_KEY",
    "STORE_ID_KEY",
    "store_id",
    "StoreSchemaError",
    "MigrationRequiredError",
    "_refuse_unmigrated",
    "migration_marker",
    "open_db_for_migration",
    "run_with_retry",
    "is_busy",
    "StoreBusyError",
    "RETRY_ATTEMPTS",
    "RETRY_DEADLINE_SECONDS",
    "RETRY_FIRST_PAUSE",
    "RETRY_PAUSE_CAP",
)


def _hold_fresh_db_reserved() -> sqlite3.Connection:
    """A second connection holding a RESERVED lock on a fresh, pre-WAL database.

    It must be `BEGIN IMMEDIATE`: SQLite fails `PRAGMA journal_mode=WAL` at once
    against a RESERVED lock without calling the busy handler, which is the race
    `open_db` retries. `BEGIN EXCLUSIVE` would make the busy handler run and so
    prove nothing.
    """
    path = paths.db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    holder = sqlite3.connect(path, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    return holder


def test_db_is_a_leaf_module_of_the_store_package():
    for function in (
        db.open_db,
        db.immediate,
        db.read_snapshot,
        db.open_db_for_reading,
        db.store_id,
        db.run_with_retry,
        db.migration_marker,
        db.open_db_for_migration,
    ):
        assert callable(function)
        assert function.__module__ == "agent_manager.store.db"
    assert db.BUSY_TIMEOUT_SECONDS == 30.0
    assert db.SCHEMA_VERSION == 1
    assert db.MIGRATED_KEY == "migrated_at"
    assert db.STORE_ID_KEY == "store_id"
    assert db.RETRY_ATTEMPTS == 5
    assert db.RETRY_DEADLINE_SECONDS == 10.0
    assert db.RETRY_FIRST_PAUSE == 0.1
    assert db.RETRY_PAUSE_CAP == 2.0
    assert db.StoreSchemaError.__module__ == "agent_manager.store.db"
    assert issubclass(db.StoreSchemaError, RuntimeError)
    assert db.MigrationRequiredError.__module__ == "agent_manager.store.db"
    assert issubclass(db.MigrationRequiredError, RuntimeError)
    assert db.StoreBusyError.__module__ == "agent_manager.store.db"
    assert issubclass(db.StoreBusyError, RuntimeError)


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
    holder = _hold_fresh_db_reserved()
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


def test_open_db_creates_the_machine_file_in_wal_mode_and_no_projects_dir(repo):
    assert not paths.data_path().exists()

    conn = db.open_db(repo)
    try:
        assert paths.db_path().is_file()
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        conn.close()

    assert not (paths.data_path() / "projects").exists()


def test_two_roots_open_one_file(repo, tmp_path):
    other = tmp_path / "other-repo"
    other.mkdir()
    first = db.open_db(repo)
    second = db.open_db(other)
    try:
        project_id = store_projects.resolve(first, repo, now=NOW)
        first.commit()
        assert store_projects.lookup(second, repo) == project_id
        machine = paths.db_path().resolve()
        assert Path(first.execute("PRAGMA database_list").fetchone()["file"]).resolve() == machine
        assert Path(second.execute("PRAGMA database_list").fetchone()["file"]).resolve() == machine
    finally:
        first.close()
        second.close()


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
    holder = _hold_fresh_db_reserved()
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
    holder = _hold_fresh_db_reserved()
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
    path = paths.db_path()
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
    assert {"projects", "runs", "stories", "subtasks", "phases", "attempts"} <= names


def test_open_db_creates_the_projects_table(repo):
    conn = db.open_db(repo)
    try:
        info = conn.execute("PRAGMA table_info(projects)").fetchall()
    finally:
        conn.close()
    assert [row["name"] for row in info] == ["id", "repo_dir", "created_at"]
    by_name = {row["name"]: row for row in info}
    assert (by_name["id"]["type"], by_name["id"]["pk"]) == ("INTEGER", 1)
    assert (by_name["repo_dir"]["notnull"], by_name["created_at"]["notnull"]) == (1, 1)


def test_projects_repo_dir_is_unique(repo):
    conn = db.open_db(repo)
    try:
        conn.execute(
            "INSERT INTO projects (repo_dir, created_at) VALUES ('/a', '2026-10-07')"
        )
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            conn.execute(
                "INSERT INTO projects (repo_dir, created_at) VALUES ('/a', '2026-10-08')"
            )
    finally:
        conn.rollback()
        conn.close()


def test_reopening_an_existing_db_keeps_its_rows(repo):
    first = db.open_db(repo)
    project_id = store_projects.resolve(first, repo, now=NOW)
    first.execute(
        "INSERT INTO runs (project_id, id, workflow, repo_dir, base_branch,"
        " branch_prefix, status, started_at, config)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (project_id, RUN_ID, "milestone", str(repo), "main", "m1/", "started", None, "{}"),
    )
    first.commit()
    first.close()

    second = db.open_db(repo)
    try:
        rows = second.execute("SELECT project_id, id, workflow FROM runs").fetchall()
    finally:
        second.close()
    assert [tuple(row) for row in rows] == [(project_id, RUN_ID, "milestone")]


def test_immediate_holds_the_write_lock_from_begin(repo):
    # Review Focus 2: BEGIN IMMEDIATE, not a deferred BEGIN, so no second
    # writer can land between reading MAX(seq) and the insert.
    conn = db.open_db(repo)
    blocker = sqlite3.connect(paths.db_path(), timeout=0)
    try:
        with db.immediate(conn):
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                blocker.execute("BEGIN IMMEDIATE")
        blocker.execute("BEGIN IMMEDIATE")
        blocker.rollback()
    finally:
        blocker.close()
        conn.close()


@pytest.mark.parametrize("table", _PROJECT_TABLES)
def test_every_project_table_has_a_not_null_project_id_first(repo, table):
    conn = db.open_db(repo)
    try:
        first = conn.execute(f"PRAGMA table_info({table})").fetchall()[0]
        references = [
            (row["from"], row["table"], row["to"])
            for row in conn.execute(f"PRAGMA foreign_key_list({table})")
        ]
    finally:
        conn.close()
    assert (first["name"], first["type"], first["notnull"]) == ("project_id", "INTEGER", 1)
    assert references == [("project_id", "projects", "id")]


@pytest.mark.parametrize("table", _PROJECT_TABLES)
def test_a_row_without_project_id_is_refused(repo, table):
    conn = db.open_db(repo)
    try:
        with pytest.raises(
            sqlite3.IntegrityError, match=f"NOT NULL constraint failed: {table}.project_id"
        ):
            _insert(conn, table, _MINIMAL_ROWS[table])
        # Non-vacuity: the same row with a project id goes in.
        project_id = store_projects.resolve(conn, repo, now=NOW)
        _insert(conn, table, {"project_id": project_id, **_MINIMAL_ROWS[table]})
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.rollback()
        conn.close()
    assert count == 1


@pytest.mark.parametrize("table", ["run_claims", "board_comments"])
def test_claims_and_comments_are_keyed_by_project_and_key(repo, table):
    conn = db.open_db(repo)
    try:
        key_columns = {
            row["name"]: row["pk"]
            for row in conn.execute(f"PRAGMA table_info({table})")
            if row["pk"]
        }
        mine = store_projects.resolve(conn, repo, now=NOW)
        theirs = store_projects.resolve(conn, repo.parent / "other-repo", now=NOW)
        _insert(conn, table, {"project_id": mine, **_MINIMAL_ROWS[table]})
        _insert(conn, table, {"project_id": theirs, **_MINIMAL_ROWS[table]})
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            _insert(conn, table, {"project_id": mine, **_MINIMAL_ROWS[table]})
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.rollback()
        conn.close()
    assert key_columns == {"project_id": 1, "key": 2}
    assert count == 2


def test_added_columns_stay_last(repo):
    conn = db.open_db(repo)
    try:
        last = {
            table: conn.execute(f"PRAGMA table_info({table})").fetchall()[-1]["name"]
            for table, _, _ in db._ADDED_COLUMNS
        }
    finally:
        conn.close()
    assert last == {"phases": "detail", "runs": "milestone_id"}


def test_open_db_for_reading_a_settled_db_creates_no_sidecars(repo):
    db.open_db(repo).close()
    sidecars = [paths.db_path().with_name(paths.db_path().name + s) for s in ("-wal", "-shm")]
    assert not any(path.exists() for path in sidecars)

    db.open_db_for_reading(repo).close()

    assert not any(path.exists() for path in sidecars)


def _user_version(path: Path) -> int:
    conn = sqlite3.connect(path)
    try:
        return conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()


def _write_newer_db(path: Path) -> bytes:
    """A bare SQLite file one schema version past this build's, with one
    sentinel table; returns its bytes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE sentinel (x)")
        conn.execute(f"PRAGMA user_version = {db.SCHEMA_VERSION + 1}")
        conn.commit()
    finally:
        conn.close()
    return path.read_bytes()


def _data_tree() -> dict[str, bytes | None]:
    """Every path under the data dir with its bytes (`None` for a directory)."""
    root = paths.data_path()
    if not root.exists():
        return {}
    return {
        str(entry.relative_to(root)): entry.read_bytes() if entry.is_file() else None
        for entry in sorted(root.rglob("*"))
    }


def test_a_new_db_is_stamped_with_the_schema_version_and_reopening_keeps_it(repo):
    db.open_db(repo).close()
    assert _user_version(paths.db_path()) == db.SCHEMA_VERSION == 1

    db.open_db(repo).close()
    assert _user_version(paths.db_path()) == 1


def test_an_unstamped_db_with_the_current_tables_is_stamped_and_keeps_its_rows(repo):
    path = paths.db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    built = sqlite3.connect(path)
    built.executescript(db._SCHEMA)
    built.execute(
        "INSERT INTO projects (repo_dir, created_at) VALUES ('/kept', ?)", (_STAMP,)
    )
    built.commit()
    built.close()
    assert _user_version(path) == 0

    conn = db.open_db(repo)
    try:
        kept = [row["repo_dir"] for row in conn.execute("SELECT repo_dir FROM projects")]
    finally:
        conn.close()

    assert kept == ["/kept"]
    assert _user_version(path) == 1


def test_open_db_refuses_a_newer_db_and_leaves_it_untouched(repo):
    path = paths.db_path()
    before = _write_newer_db(path)

    with pytest.raises(db.StoreSchemaError) as raised:
        db.open_db(repo)

    message = str(raised.value)
    assert str(path) in message
    assert str(db.SCHEMA_VERSION + 1) in message
    assert str(db.SCHEMA_VERSION) in message
    assert "older" in message
    assert path.read_bytes() == before
    assert _user_version(path) == db.SCHEMA_VERSION + 1
    conn = sqlite3.connect(path)
    try:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master")}
    finally:
        conn.close()
    assert tables == {"sentinel"}


def test_open_db_for_reading_refuses_a_newer_db_and_creates_nothing(repo):
    _write_newer_db(paths.db_path())
    before = _data_tree()

    with pytest.raises(db.StoreSchemaError):
        db.open_db_for_reading(repo)

    assert _data_tree() == before


def test_open_db_creates_the_meta_table(repo):
    conn = db.open_db(repo)
    try:
        info = conn.execute("PRAGMA table_info(meta)").fetchall()
    finally:
        conn.close()
    assert [row["name"] for row in info] == ["key", "value"]
    by_name = {row["name"]: row for row in info}
    assert (by_name["key"]["type"], by_name["key"]["pk"]) == ("TEXT", 1)
    assert (by_name["value"]["type"], by_name["value"]["notnull"]) == ("TEXT", 1)


_HEX_ID = re.compile(r"[0-9a-f]{32}")


def _store_id_rows(path: Path) -> list[str]:
    """Every `meta` value stored under `STORE_ID_KEY`, read on a fresh connection."""
    conn = sqlite3.connect(path)
    try:
        return [
            row[0]
            for row in conn.execute(
                "SELECT value FROM meta WHERE key = ?", (db.STORE_ID_KEY,)
            )
        ]
    finally:
        conn.close()


def _write_stamped_db_without_a_store_id(path: Path) -> None:
    """A current-schema `am.db` at `SCHEMA_VERSION`, as 1.1.3 left it: no
    `store_id` row, rollback-journal mode, no sidecars."""
    path.parent.mkdir(parents=True, exist_ok=True)
    built = sqlite3.connect(path)
    try:
        built.executescript(db._SCHEMA)
        built.execute(f"PRAGMA user_version = {db.SCHEMA_VERSION}")
        built.commit()
    finally:
        built.close()


def _read_store_id(opener) -> str | None:
    conn = opener(paths.data_path())
    try:
        return db.store_id(conn)
    finally:
        conn.close()


def test_open_db_mints_a_store_id(repo):
    conn = db.open_db(repo)
    try:
        minted = db.store_id(conn)
        assert not conn.in_transaction
    finally:
        conn.close()

    assert minted is not None
    assert _HEX_ID.fullmatch(minted)
    assert _store_id_rows(paths.db_path()) == [minted]


def test_store_id_is_stable_across_reopen(repo):
    first = _read_store_id(db.open_db)
    second = _read_store_id(db.open_db)
    third = _read_store_id(db.open_db)

    assert first is not None
    assert first == second == third
    assert _store_id_rows(paths.db_path()) == [first]


def test_store_id_differs_across_fresh_databases(repo, tmp_path, monkeypatch):
    first_path = paths.db_path()
    first = _read_store_id(db.open_db)

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "other"))
    second_path = paths.db_path()
    second = _read_store_id(db.open_db)

    assert first_path != second_path
    assert first_path.is_file() and second_path.is_file()
    assert first is not None and second is not None
    assert first != second


def test_racing_first_opens_agree_on_one_store_id(repo):
    racers = 4
    barrier = threading.Barrier(racers)
    lock = threading.Lock()
    seen: list[str | None] = []
    errors: list[BaseException] = []

    def first_open() -> None:
        try:
            barrier.wait(timeout=10)
            conn = db.open_db(repo)
            try:
                value = db.store_id(conn)
            finally:
                conn.close()
            with lock:
                seen.append(value)
        except BaseException as error:  # surfaced by the assertion below
            with lock:
                errors.append(error)

    assert not paths.db_path().exists()
    threads = [threading.Thread(target=first_open) for _ in range(racers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert errors == []
    assert len(seen) == racers
    assert len(set(seen)) == 1
    assert seen[0] is not None
    assert _store_id_rows(paths.db_path()) == [seen[0]]


def test_open_db_backfills_a_store_id_into_a_stamped_db_without_one(repo):
    path = paths.db_path()
    _write_stamped_db_without_a_store_id(path)
    assert _store_id_rows(path) == []

    minted = _read_store_id(db.open_db)

    assert minted is not None
    assert _HEX_ID.fullmatch(minted)
    assert _store_id_rows(path) == [minted]
    assert _user_version(path) == db.SCHEMA_VERSION == 1


def test_store_id_is_none_on_an_in_memory_read_projection(repo):
    assert not paths.data_path().exists()

    assert _read_store_id(db.open_db_for_reading) is None

    assert not paths.data_path().exists()


def test_open_db_for_reading_reads_the_store_id_open_db_minted(repo):
    minted = _read_store_id(db.open_db)

    assert _read_store_id(db.open_db_for_reading) == minted


def test_open_db_for_reading_does_not_mint_into_an_existing_db(repo):
    path = paths.db_path()
    _write_stamped_db_without_a_store_id(path)
    before = _data_tree()

    assert _read_store_id(db.open_db_for_reading) is None

    assert _data_tree() == before
    assert _store_id_rows(path) == []


def test_store_id_reads_the_same_with_a_tuple_row_factory(repo):
    conn = db.open_db(repo)
    try:
        with_rows = db.store_id(conn)
        conn.row_factory = None
        with_tuples = db.store_id(conn)
    finally:
        conn.close()

    assert with_rows is not None
    assert with_tuples == with_rows


def test_open_db_for_reading_mints_when_it_falls_through_to_open_db(repo):
    # An unstamped file is "an older schema": open_db_for_reading hands it to
    # open_db, which stamps it and mints in the same pass.
    path = paths.db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    built = sqlite3.connect(path)
    built.executescript(db._SCHEMA)
    built.commit()
    built.close()
    assert _user_version(path) == 0

    read = _read_store_id(db.open_db_for_reading)

    assert read is not None
    assert _store_id_rows(path) == [read]
    assert _user_version(path) == db.SCHEMA_VERSION


def test_open_db_never_changes_an_existing_store_id(repo):
    path = paths.db_path()
    _write_stamped_db_without_a_store_id(path)
    conn = sqlite3.connect(path)
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?)", (db.STORE_ID_KEY, "kept-as-is")
    )
    conn.commit()
    conn.close()

    assert _read_store_id(db.open_db) == "kept-as-is"
    assert _read_store_id(db.open_db) == "kept-as-is"
    assert _store_id_rows(path) == ["kept-as-is"]


def test_the_minted_store_id_is_committed_before_open_db_returns(repo):
    conn = db.open_db(repo)
    try:
        assert not conn.in_transaction
        assert _store_id_rows(paths.db_path()) == [db.store_id(conn)]
    finally:
        conn.close()


def test_open_db_for_reading_stamps_an_unstamped_db_and_reads_its_rows(repo):
    # Review Focus 3: the current tables at user_version 0 fall through to
    # `open_db`, which stamps the file; the rows are still read.
    path = paths.db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    built = sqlite3.connect(path)
    built.executescript(db._SCHEMA)
    built.execute(
        "INSERT INTO projects (repo_dir, created_at) VALUES ('/kept', ?)", (_STAMP,)
    )
    built.commit()
    built.close()

    conn = db.open_db_for_reading(repo)
    try:
        kept = [row["repo_dir"] for row in conn.execute("SELECT repo_dir FROM projects")]
    finally:
        conn.close()

    assert kept == ["/kept"]
    assert _user_version(path) == db.SCHEMA_VERSION


def _leave_legacy(name: str = "x.db") -> Path:
    """A per-project database from an older `am`, as `legacy_project_dbs` lists it."""
    projects = paths.data_path() / "projects"
    projects.mkdir(parents=True, exist_ok=True)
    legacy = projects / name
    legacy.write_bytes(b"legacy bytes")
    return legacy


def _mark_migrated(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?)", (db.MIGRATED_KEY, _STAMP)
        )
        conn.commit()
    finally:
        conn.close()


_OPENERS = pytest.mark.parametrize(
    "opener", [db.open_db, db.open_db_for_reading], ids=["open_db", "open_db_for_reading"]
)


@_OPENERS
def test_legacy_dbs_and_no_am_db_refuse_and_change_nothing(repo, opener):
    _leave_legacy("a.db")
    _leave_legacy("b.db")
    before = _data_tree()

    with pytest.raises(db.MigrationRequiredError) as raised:
        opener(repo)

    message = str(raised.value)
    assert "am migrate" in message
    assert str(paths.data_path() / "projects") in message
    assert "2 " in message
    assert "Nothing has been changed" in message
    assert _data_tree() == before
    for name in ("am.db", "am.db-wal", "am.db-shm"):
        assert not (paths.data_path() / name).exists()


@_OPENERS
def test_legacy_dbs_and_an_am_db_without_the_marker_refuse(repo, opener):
    db.open_db(repo).close()
    _leave_legacy()
    before = paths.db_path().read_bytes()

    with pytest.raises(db.MigrationRequiredError):
        opener(repo)

    assert paths.db_path().read_bytes() == before


@_OPENERS
def test_legacy_dbs_and_an_am_db_without_a_meta_table_refuse(repo, opener):
    path = paths.db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    bare = sqlite3.connect(path)
    bare.execute("CREATE TABLE sentinel (x)")
    bare.commit()
    bare.close()
    _leave_legacy()

    with pytest.raises(db.MigrationRequiredError):
        opener(repo)


@_OPENERS
def test_legacy_dbs_and_the_marker_open_normally(repo, opener):
    db.open_db(repo).close()
    _mark_migrated(paths.db_path())
    legacy = _leave_legacy()

    conn = opener(repo)
    try:
        marker = conn.execute(
            "SELECT value FROM meta WHERE key = ?", (db.MIGRATED_KEY,)
        ).fetchone()[0]
    finally:
        conn.close()
    assert marker == _STAMP
    assert legacy.read_bytes() == b"legacy bytes"


def test_the_migrated_marker_and_the_store_id_coexist(repo):
    minted = _read_store_id(db.open_db)
    _mark_migrated(paths.db_path())
    _leave_legacy()

    conn = db.open_db(repo)
    try:
        again = db.store_id(conn)
        marker = conn.execute(
            "SELECT value FROM meta WHERE key = ?", (db.MIGRATED_KEY,)
        ).fetchone()[0]
    finally:
        conn.close()

    assert again == minted
    assert marker == _STAMP


def test_lock_files_sidecars_and_directories_under_projects_never_refuse(repo):
    projects = paths.data_path() / "projects"
    projects.mkdir(parents=True)
    (projects / f"{'0' * 64}.events.lock").write_bytes(b"")
    (projects / "x.db-wal").write_bytes(b"")
    (projects / "sub.db").mkdir()

    db.open_db(repo).close()

    assert paths.db_path().is_file()


def test_the_refusal_is_checked_before_the_schema_version(repo):
    _write_newer_db(paths.db_path())
    _leave_legacy()

    with pytest.raises(db.MigrationRequiredError):
        db.open_db(repo)


def test_store_open_on_an_unmigrated_machine_refuses_before_any_run_directory(repo):
    # Review Focus 1: `Store.open` opens the projection before it builds a
    # `Journal`, so a refused write leaves no `runs/<run id>` behind.
    _leave_legacy()

    with pytest.raises(db.MigrationRequiredError):
        Store.open(repo, RUN_ID)

    assert not (paths.data_path() / "runs").exists()
    assert not paths.db_path().exists()


def test_the_refusal_check_does_not_wait_for_a_writer_holding_a_transaction(repo):
    # Review Focus 2: the marker and version are read `mode=ro` under WAL,
    # so a writer's open transaction never blocks them.
    writer = db.open_db(repo)
    _mark_migrated(paths.db_path())
    _leave_legacy()
    try:
        writer.execute("BEGIN IMMEDIATE")
        writer.execute(
            "INSERT INTO projects (repo_dir, created_at) VALUES ('/held', ?)", (_STAMP,)
        )
        started = time.monotonic()
        reader = db.open_db_for_reading(repo)
        try:
            assert reader.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 0
        finally:
            reader.close()
        assert time.monotonic() - started < 1.0
    finally:
        writer.rollback()
        writer.close()


def test_migration_marker_is_none_and_creates_nothing_without_an_am_db(repo):
    assert db.migration_marker(paths.db_path()) is None
    assert not paths.data_path().exists()


def test_migration_marker_is_none_without_a_meta_table_or_without_its_row(repo):
    path = paths.db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    bare = sqlite3.connect(path)
    bare.execute("CREATE TABLE sentinel (x)")
    bare.commit()
    bare.close()
    assert db.migration_marker(path) is None

    path.unlink()
    db.open_db(repo).close()
    assert db.migration_marker(path) is None


def test_migration_marker_reads_the_value_and_changes_nothing(repo):
    db.open_db(repo).close()
    _mark_migrated(paths.db_path())
    _leave_legacy()
    before = _data_tree()

    assert db.migration_marker(paths.db_path()) == _STAMP
    assert _data_tree() == before


def test_open_db_for_migration_creates_the_full_schema_on_an_unmigrated_machine(repo):
    legacy = _leave_legacy()

    conn = db.open_db_for_migration()
    try:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        minted = db.store_id(conn)
        row_type = type(conn.execute("SELECT 1 AS one").fetchone())
    finally:
        conn.close()

    assert {"projects", "meta", "events", *_PROJECT_TABLES} <= tables
    assert mode == "wal"
    assert minted is not None
    assert row_type is sqlite3.Row
    assert _user_version(paths.db_path()) == db.SCHEMA_VERSION
    assert db.migration_marker(paths.db_path()) is None
    assert legacy.read_bytes() == b"legacy bytes"


def test_open_db_for_migration_refuses_a_newer_db_and_leaves_it_untouched(repo):
    _leave_legacy()
    path = paths.db_path()
    before = _write_newer_db(path)

    with pytest.raises(db.StoreSchemaError):
        db.open_db_for_migration()

    assert path.read_bytes() == before


# -- run_with_retry ------------------------------------------------------------


class _FakeClock:
    """`clock` and `sleep` for `run_with_retry` that agree: a pause advances `now`."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.pauses: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.pauses.append(seconds)
        self.now += seconds


def _coded(code: int, message: str = "database is locked") -> sqlite3.OperationalError:
    """An `OperationalError` carrying `sqlite_errorcode`, as SQLite's own errors do."""
    error = sqlite3.OperationalError(message)
    error.sqlite_errorcode = code
    return error


def _retry(job, fake: _FakeClock, *, rng: Callable[[], float] = lambda: 0.5):
    return db.run_with_retry(
        job, operation="beat", clock=fake.clock, sleep=fake.sleep, rng=rng
    )


def test_run_with_retry_returns_the_jobs_value_without_sleeping():
    fake = _FakeClock()
    draws: list[float] = []

    def rng() -> float:
        draws.append(0.5)
        return 0.5

    assert _retry(lambda: "written", fake, rng=rng) == "written"
    assert fake.pauses == []
    assert draws == []


def test_run_with_retry_retries_busy_then_returns():
    fake = _FakeClock()
    calls: list[int] = []

    def job() -> str:
        calls.append(1)
        if len(calls) <= 2:
            raise _coded(sqlite3.SQLITE_BUSY)
        return "written"

    assert _retry(job, fake) == "written"
    assert len(calls) == 3
    # rng 0.5: each pause is 0.75 of its base (0.1, then 0.2).
    assert fake.pauses == pytest.approx([0.075, 0.15])


@pytest.mark.parametrize(
    "code",
    [sqlite3.SQLITE_LOCKED, 517, 773, 262],
    ids=["LOCKED", "BUSY_SNAPSHOT", "BUSY_TIMEOUT", "LOCKED_SHAREDCACHE"],
)
def test_run_with_retry_retries_locked_and_extended_busy_codes(code):
    # Review Focus 1: extended codes are masked with `& 0xFF`.
    fake = _FakeClock()
    calls: list[int] = []

    def job() -> str:
        calls.append(1)
        if len(calls) == 1:
            raise _coded(code)
        return "written"

    assert _retry(job, fake) == "written"
    assert len(calls) == 2
    assert len(fake.pauses) == 1


def test_run_with_retry_gives_up_after_the_attempt_budget():
    # Review Focus 3: exhaustion chains the last error, not the first.
    fake = _FakeClock()
    raised: list[sqlite3.OperationalError] = []

    def job() -> None:
        error = _coded(sqlite3.SQLITE_BUSY)
        raised.append(error)
        raise error

    with pytest.raises(db.StoreBusyError) as caught:
        _retry(job, fake)

    assert len(raised) == db.RETRY_ATTEMPTS == 5
    assert caught.value.__cause__ is raised[-1]
    assert caught.value.__cause__ is not raised[0]
    assert caught.value.operation == "beat"
    assert caught.value.attempts == 5
    assert caught.value.elapsed == pytest.approx(0.075 + 0.15 + 0.3 + 0.6)
    message = str(caught.value)
    assert "beat" in message
    assert "5 attempt" in message
    assert "10 s" in message
    assert len(fake.pauses) == db.RETRY_ATTEMPTS - 1


def test_run_with_retry_stops_at_the_deadline_before_the_attempt_budget():
    # Review Focus 4: the deadline is checked before sleeping.
    fake = _FakeClock()
    calls: list[int] = []

    def job() -> None:
        calls.append(1)
        fake.now += 6.0  # blocked in `busy_timeout` before SQLite gave up
        raise _coded(sqlite3.SQLITE_BUSY)

    with pytest.raises(db.StoreBusyError) as caught:
        _retry(job, fake)

    assert len(calls) == 2
    assert caught.value.attempts == 2
    assert caught.value.elapsed == pytest.approx(12.075)
    assert fake.pauses == pytest.approx([0.075])


def test_run_with_retry_clamps_the_pause_to_the_time_left():
    fake = _FakeClock()
    start = fake.now
    calls: list[int] = []

    def job() -> str:
        calls.append(1)
        if len(calls) == 2:
            fake.now = start + 9.95
        if len(calls) <= 2:
            raise _coded(sqlite3.SQLITE_BUSY)
        return "written"

    assert _retry(job, fake) == "written"
    # The second jittered pause would be 0.15; only 0.05 s of budget is left.
    assert fake.pauses == pytest.approx([0.075, 0.05])


def test_run_with_retry_backoff_doubles_is_jittered_and_capped(monkeypatch):
    monkeypatch.setattr(db, "RETRY_ATTEMPTS", 8)
    monkeypatch.setattr(db, "RETRY_DEADLINE_SECONDS", 100.0)
    bases = [0.1, 0.2, 0.4, 0.8, 1.6, 2.0, 2.0]

    def always_busy() -> None:
        raise _coded(sqlite3.SQLITE_BUSY)

    low = _FakeClock()
    with pytest.raises(db.StoreBusyError):
        _retry(always_busy, low, rng=lambda: 0.0)
    assert low.pauses == pytest.approx([0.05, 0.1, 0.2, 0.4, 0.8, 1.0, 1.0])

    high = _FakeClock()
    with pytest.raises(db.StoreBusyError):
        _retry(always_busy, high, rng=lambda: 0.999999)
    assert high.pauses == pytest.approx(bases, rel=1e-5)
    assert all(pause < base for pause, base in zip(high.pauses, bases, strict=True))


def test_run_with_retry_reads_the_budget_at_call_time(monkeypatch):
    monkeypatch.setattr(db, "RETRY_ATTEMPTS", 2)
    fake = _FakeClock()
    calls: list[int] = []

    def job() -> None:
        calls.append(1)
        raise _coded(sqlite3.SQLITE_BUSY)

    with pytest.raises(db.StoreBusyError) as caught:
        _retry(job, fake)

    assert len(calls) == 2
    assert caught.value.attempts == 2
    assert "2 attempts within" in str(caught.value)


def _raises(error: BaseException) -> Callable[[], None]:
    def action() -> None:
        raise error

    return action


def _uncoded_none() -> sqlite3.OperationalError:
    error = sqlite3.OperationalError("database is locked")
    error.sqlite_errorcode = None
    return error


def _update_an_event() -> None:
    """An `UPDATE` on `events`, refused by the append-only trigger."""
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(db._SCHEMA)
        conn.execute(
            "INSERT INTO projects (repo_dir, created_at) VALUES ('/repo', ?)", (_STAMP,)
        )
        conn.execute(
            "INSERT INTO events (project_id, run_id, run_seq, ts, kind, payload, source)"
            " VALUES (1, ?, 0, ?, 'run_started', '{}', 'live')",
            (RUN_ID, _STAMP),
        )
        conn.execute("UPDATE events SET kind = 'changed'")
    finally:
        conn.close()


@pytest.mark.parametrize(
    ("action", "kind"),
    [
        (
            _raises(_coded(sqlite3.SQLITE_READONLY, "attempt to write a readonly database")),
            sqlite3.OperationalError,
        ),
        (_raises(_coded(sqlite3.SQLITE_ERROR, "no such table: nope")), sqlite3.OperationalError),
        (_raises(sqlite3.OperationalError("database is locked")), sqlite3.OperationalError),
        (_raises(_uncoded_none()), sqlite3.OperationalError),
        (
            _raises(sqlite3.IntegrityError("UNIQUE constraint failed: projects.repo_dir")),
            sqlite3.IntegrityError,
        ),
        (_raises(db.StoreSchemaError(Path("/data/am.db"), 2)), db.StoreSchemaError),
        (_raises(db.StoreBusyError("beat", 5, 10.0)), db.StoreBusyError),
        (_raises(ValueError("not a card id")), ValueError),
        (_update_an_event, sqlite3.IntegrityError),
    ],
    ids=[
        "READONLY",
        "ERROR",
        "uncoded-locked",
        "code-None",
        "IntegrityError",
        "StoreSchemaError",
        "StoreBusyError",
        "ValueError",
        "append-only-trigger",
    ],
)
def test_run_with_retry_does_not_retry_other_errors(action, kind):
    # Review Focus 2: an uncoded "database is locked" is not retried, so no
    # string match creeps back in.
    fake = _FakeClock()
    seen: list[BaseException] = []

    def job() -> None:
        try:
            action()
        except BaseException as error:
            seen.append(error)
            raise

    with pytest.raises(kind) as caught:
        _retry(job, fake)

    assert type(caught.value) is kind
    assert len(seen) == 1
    assert caught.value is seen[0]
    assert caught.value.__cause__ is None
    assert fake.pauses == []


@pytest.mark.parametrize(
    "error",
    [store_leases.LeaseLostError(RUN_ID, None), KeyboardInterrupt(), SystemExit(3)],
    ids=["LeaseLostError", "KeyboardInterrupt", "SystemExit"],
)
def test_run_with_retry_lets_lease_lost_and_other_base_exceptions_through(error):
    # Review Focus 5: identity, so no `except BaseException` and no `from`.
    fake = _FakeClock()
    calls: list[int] = []

    def job() -> None:
        calls.append(1)
        raise error

    with pytest.raises(type(error)) as caught:
        _retry(job, fake)

    assert caught.value is error
    assert caught.value.__cause__ is None
    assert len(calls) == 1
    assert fake.pauses == []


def test_run_with_retry_lets_an_interrupt_during_a_pause_through():
    interrupt = KeyboardInterrupt()
    calls: list[int] = []

    def job() -> None:
        calls.append(1)
        raise _coded(sqlite3.SQLITE_BUSY)

    def sleep(seconds: float) -> None:
        raise interrupt

    with pytest.raises(KeyboardInterrupt) as caught:
        db.run_with_retry(
            job, operation="beat", clock=lambda: 0.0, sleep=sleep, rng=lambda: 0.5
        )

    assert caught.value is interrupt
    assert len(calls) == 1


def test_run_with_retry_with_no_time_left_raises_after_one_attempt_without_sleeping(
    monkeypatch,
):
    monkeypatch.setattr(db, "RETRY_DEADLINE_SECONDS", 0.0)
    fake = _FakeClock()
    calls: list[int] = []

    def job() -> None:
        calls.append(1)
        raise _coded(sqlite3.SQLITE_BUSY)

    with pytest.raises(db.StoreBusyError) as caught:
        _retry(job, fake)

    assert len(calls) == 1
    assert caught.value.attempts == 1
    assert fake.pauses == []


@pytest.mark.parametrize("value", [None, 0, "", []], ids=["None", "zero", "empty-str", "empty-list"])
def test_run_with_retry_returns_a_falsy_value_unchanged(value):
    fake = _FakeClock()
    calls: list[int] = []

    def job():
        calls.append(1)
        return value

    assert _retry(job, fake) is value
    assert len(calls) == 1
    assert fake.pauses == []


def test_run_with_retry_reruns_an_immediate_write_refused_by_a_real_lock(tmp_path):
    # A real `BEGIN IMMEDIATE` refused by another connection's lock carries
    # `SQLITE_BUSY`; re-running the whole job leaves exactly one row.
    path = tmp_path / "contended.db"
    holder = sqlite3.connect(path, isolation_level=None, timeout=0)
    holder.execute("CREATE TABLE t (x INTEGER)")
    holder.execute("BEGIN IMMEDIATE")
    writer = sqlite3.connect(path, timeout=0)
    calls: list[int] = []

    def job() -> None:
        calls.append(1)
        with db.immediate(writer):
            writer.execute("INSERT INTO t VALUES (1)")

    def release(seconds: float) -> None:
        if holder.in_transaction:
            holder.execute("ROLLBACK")

    try:
        db.run_with_retry(
            job, operation="insert", clock=lambda: 0.0, sleep=release, rng=lambda: 0.5
        )
        assert len(calls) == 2
        assert writer.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 1
    finally:
        writer.close()
        holder.close()


def test_open_reader_is_read_only_and_sees_committed_rows(repo):
    writer = db.open_db(repo)
    reader = db.open_reader(paths.db_path())
    try:
        writer.execute("INSERT INTO meta (key, value) VALUES ('committed', 'v')")
        writer.commit()
        # Left open: an implicit transaction the reader must not see into.
        writer.execute("INSERT INTO meta (key, value) VALUES ('uncommitted', 'v')")
        keys = {row["key"] for row in reader.execute("SELECT key FROM meta")}
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            reader.execute("INSERT INTO meta (key, value) VALUES ('refused', 'v')")
    finally:
        writer.rollback()
        reader.close()
        writer.close()

    assert "committed" in keys
    assert "uncommitted" not in keys


def _append_event(writer: sqlite3.Connection, project_id: int) -> int:
    """Commit one event on `writer` under `immediate` and return its `seq`."""
    with db.immediate(writer):
        row = store_events.insert(
            writer,
            project_id=project_id,
            run_id=RUN_ID,
            ts=_STAMP,
            kind="run_upsert",
            payload={},
            source="live",
        )
    return row.seq


def _live_writer(repo: Path) -> tuple[sqlite3.Connection, int]:
    """A writing connection the caller keeps open, with `repo`'s project row
    and one event committed.

    While it is open the `-wal` and `-shm` sidecars exist, so
    `open_db_for_reading` opens `mode=ro` and later commits are visible to a
    reader; with every connection closed it would open `immutable=1`, which
    never sees a later commit and would make a snapshot test vacuous.
    """
    writer = db.open_db(repo)
    project_id = store_projects.resolve(writer, repo, now=NOW)
    writer.commit()
    _append_event(writer, project_id)
    return writer, project_id


def _count_events(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]


def test_read_snapshot_leaves_no_transaction_open(repo):
    writer, _ = _live_writer(repo)
    reader = db.open_db_for_reading(repo)
    try:
        assert reader.in_transaction is False
        with db.read_snapshot(reader) as snapshot:
            assert snapshot is reader
            assert store_events.head(reader) == 1
            assert reader.in_transaction is True
        assert reader.in_transaction is False
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            reader.execute("DELETE FROM projects")
    finally:
        reader.close()
        writer.close()


def test_read_snapshot_hides_a_commit_made_after_its_first_read(repo):
    writer, project_id = _live_writer(repo)
    reader = db.open_db_for_reading(repo)
    try:
        with db.read_snapshot(reader):
            first = store_events.head(reader)
            count = _count_events(reader)
            added = _append_event(writer, project_id)
            assert added > first
            assert store_events.head(reader) == first
            assert _count_events(reader) == count
        assert store_events.head(reader) == added
    finally:
        reader.close()
        writer.close()


def test_read_snapshot_sees_a_commit_made_before_its_first_read(repo):
    writer, project_id = _live_writer(repo)
    reader = db.open_db_for_reading(repo)
    try:
        with db.read_snapshot(reader):
            added = _append_event(writer, project_id)
            assert store_events.head(reader) == added
    finally:
        reader.close()
        writer.close()


def test_without_read_snapshot_a_later_statement_sees_a_concurrent_commit(repo):
    # The negative control for the two tests above: the fixture is live, and
    # legacy-mode sqlite3 gives no snapshot across statements on its own.
    writer, project_id = _live_writer(repo)
    reader = db.open_db_for_reading(repo)
    try:
        first = store_events.head(reader)
        added = _append_event(writer, project_id)
        assert added > first
        assert store_events.head(reader) == added
    finally:
        reader.close()
        writer.close()


def test_read_snapshot_rolls_back_and_reraises_on_error(repo):
    writer, _ = _live_writer(repo)
    reader = db.open_db_for_reading(repo)
    error = ValueError("boom")
    try:
        with pytest.raises(ValueError) as caught:
            with db.read_snapshot(reader):
                store_events.head(reader)
                raise error
        assert caught.value is error
        assert reader.in_transaction is False
    finally:
        reader.close()
        writer.close()


def test_read_snapshot_refuses_an_open_transaction(repo):
    conn = db.open_db(repo)
    try:
        conn.execute(
            "INSERT INTO projects (repo_dir, created_at) VALUES (?, ?)",
            ("/elsewhere", _STAMP),
        )
        assert conn.in_transaction is True
        with pytest.raises(sqlite3.OperationalError, match="within a transaction"):
            with db.read_snapshot(conn):
                pytest.fail("the block must not run")
        assert conn.in_transaction is True
        assert conn.execute(
            "SELECT COUNT(*) FROM projects WHERE repo_dir = '/elsewhere'"
        ).fetchone()[0] == 1
        conn.rollback()
    finally:
        conn.close()
    check = db.open_db(repo)
    try:
        assert check.execute(
            "SELECT COUNT(*) FROM projects WHERE repo_dir = '/elsewhere'"
        ).fetchone()[0] == 0
    finally:
        check.close()


def test_read_snapshot_on_the_in_memory_projection(repo):
    assert not paths.db_path().exists()
    conn = db.open_db_for_reading(repo)
    try:
        with db.read_snapshot(conn):
            assert store_events.head(conn) == 0
        assert conn.in_transaction is False
    finally:
        conn.close()
    assert not paths.db_path().exists()


def test_read_snapshot_on_an_immutable_connection(repo):
    writer, _ = _live_writer(repo)
    writer.close()
    sidecars = [paths.db_path().with_name(paths.db_path().name + s) for s in ("-wal", "-shm")]
    assert not any(path.exists() for path in sidecars)
    conn = db.open_db_for_reading(repo)
    try:
        with db.read_snapshot(conn):
            assert store_events.head(conn) == 1
        assert conn.in_transaction is False
    finally:
        conn.close()
    assert not any(path.exists() for path in sidecars)
