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
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent_manager import paths, store
from agent_manager.store import db
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
    "open_db_for_reading",
    "BUSY_TIMEOUT_SECONDS",
    "_SCHEMA",
    "_ADDED_COLUMNS",
    "_enable_wal",
    "SCHEMA_VERSION",
    "MIGRATED_KEY",
    "StoreSchemaError",
    "MigrationRequiredError",
    "_refuse_unmigrated",
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
    for function in (db.open_db, db.immediate, db.open_db_for_reading):
        assert callable(function)
        assert function.__module__ == "agent_manager.store.db"
    assert db.BUSY_TIMEOUT_SECONDS == 30.0
    assert db.SCHEMA_VERSION == 1
    assert db.MIGRATED_KEY == "migrated_at"
    assert db.StoreSchemaError.__module__ == "agent_manager.store.db"
    assert issubclass(db.StoreSchemaError, RuntimeError)
    assert db.MigrationRequiredError.__module__ == "agent_manager.store.db"
    assert issubclass(db.MigrationRequiredError, RuntimeError)


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
