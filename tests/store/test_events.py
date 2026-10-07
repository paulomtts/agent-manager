"""Behaviour of the `events` table and of `agent_manager.store.events`: the
append-only rows, their DDL in `store.db`, and where the module sits.

Real SQLite files under `tmp_path` through the `repo` and `conns` fixtures of
`tests/store/conftest.py`; nothing spawns a process, so these are unit tests.
"""

import sqlite3

import pytest

from agent_manager import paths
from agent_manager.store import db as store_db

TS = "2026-10-07T12:00:00+00:00"


def _raw_insert(
    conn: sqlite3.Connection,
    project_id: int,
    *,
    run_id: str = "run-a",
    run_seq: int = 1,
    source: str = "live",
) -> int:
    """Insert one `events` row by hand and return its `seq`; does not commit."""
    cursor = conn.execute(
        "INSERT INTO events (project_id, run_id, run_seq, ts, kind, payload, source)"
        " VALUES (?, ?, ?, ?, 'run_started', '{}', ?)",
        (project_id, run_id, run_seq, TS, source),
    )
    return cursor.lastrowid


def _count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]


def _events_objects(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    return [
        (row["type"], row["name"])
        for row in conn.execute(
            "SELECT type, name FROM sqlite_master WHERE tbl_name = 'events'"
            " ORDER BY type, name"
        )
    ]


_EVENTS_OBJECTS = [
    ("index", "events_project_seq"),
    ("index", "events_run_seq"),
    ("index", "sqlite_autoindex_events_1"),
    ("table", "events"),
    ("trigger", "events_no_delete"),
    ("trigger", "events_no_update"),
]


# ── schema: the DDL lives in store/db.py and is checked through open_db ─────


def test_events_table_has_the_designed_columns(conns):
    conn, _ = conns
    columns = [
        (row["name"], row["type"], row["notnull"], row["dflt_value"], row["pk"])
        for row in conn.execute("PRAGMA table_info(events)")
    ]
    assert columns == [
        ("seq", "INTEGER", 0, None, 1),
        ("project_id", "INTEGER", 1, None, 0),
        ("run_id", "TEXT", 1, None, 0),
        ("run_seq", "INTEGER", 1, None, 0),
        ("ts", "TEXT", 1, None, 0),
        ("kind", "TEXT", 1, None, 0),
        ("story_id", "TEXT", 0, None, 0),
        ("card_id", "TEXT", 0, None, 0),
        ("phase", "TEXT", 0, None, 0),
        ("attempt", "INTEGER", 0, None, 0),
        ("schema", "INTEGER", 1, "1", 0),
        ("payload", "TEXT", 1, None, 0),
        ("source", "TEXT", 1, None, 0),
    ]
    references = [
        (row["from"], row["table"], row["to"])
        for row in conn.execute("PRAGMA foreign_key_list(events)")
    ]
    assert references == [("project_id", "projects", "id")]


def test_events_seq_is_autoincrement(conns, project_id):
    conn, _ = conns
    sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'events'"
    ).fetchone()[0]
    assert "AUTOINCREMENT" in sql

    seq = _raw_insert(conn, project_id)
    conn.commit()

    assert conn.execute(
        "SELECT seq FROM sqlite_sequence WHERE name = 'events'"
    ).fetchone()[0] == seq


def test_events_indexes_cover_run_and_project_by_seq(conns):
    conn, _ = conns
    indexes = {
        row["name"]: [
            column["name"]
            for column in conn.execute(f"PRAGMA index_info({row['name']})")
        ]
        for row in conn.execute("PRAGMA index_list(events)")
    }
    assert indexes == {
        "events_run_seq": ["run_id", "seq"],
        "events_project_seq": ["project_id", "seq"],
        "sqlite_autoindex_events_1": ["run_id", "run_seq"],
    }


def test_update_of_an_event_is_refused(conns, project_id):
    conn, _ = conns
    seq = _raw_insert(conn, project_id)
    conn.commit()

    with pytest.raises(sqlite3.IntegrityError, match="^events are append-only$"):
        conn.execute("UPDATE events SET kind = 'rewritten' WHERE seq = ?", (seq,))
    conn.rollback()

    assert conn.execute(
        "SELECT kind FROM events WHERE seq = ?", (seq,)
    ).fetchone()[0] == "run_started"


def test_delete_of_an_event_is_refused(conns, project_id):
    conn, _ = conns
    _raw_insert(conn, project_id)
    conn.commit()

    with pytest.raises(sqlite3.IntegrityError, match="^events are append-only$"):
        conn.execute("DELETE FROM events")
    conn.rollback()

    assert _count(conn) == 1


def test_update_or_delete_matching_no_row_is_not_an_error(conns, project_id):
    conn, _ = conns
    _raw_insert(conn, project_id)
    conn.commit()

    conn.execute("UPDATE events SET kind = 'x' WHERE seq = -1")
    conn.execute("DELETE FROM events WHERE seq = -1")
    conn.commit()

    assert _count(conn) == 1


def test_the_triggers_hold_on_another_connection(conns, project_id):
    conn, other = conns
    seq = _raw_insert(conn, project_id)
    conn.commit()

    with pytest.raises(sqlite3.IntegrityError, match="^events are append-only$"):
        other.execute("UPDATE events SET payload = '{\"x\": 1}' WHERE seq = ?", (seq,))
    other.rollback()
    with pytest.raises(sqlite3.IntegrityError, match="^events are append-only$"):
        other.execute("DELETE FROM events WHERE seq = ?", (seq,))
    other.rollback()

    assert _count(other) == 1
    assert other.execute(
        "SELECT payload FROM events WHERE seq = ?", (seq,)
    ).fetchone()[0] == "{}"


@pytest.mark.parametrize("source", ["live", "imported"])
def test_live_and_imported_sources_are_accepted(conns, project_id, source):
    conn, _ = conns
    _raw_insert(conn, project_id, source=source)
    conn.commit()
    assert _count(conn) == 1


def test_source_outside_live_and_imported_is_refused(conns, project_id):
    conn, _ = conns
    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
        _raw_insert(conn, project_id, source="replayed")
    conn.rollback()
    assert _count(conn) == 0


def test_a_raw_duplicate_run_seq_is_refused(conns, project_id):
    conn, _ = conns
    _raw_insert(conn, project_id, run_id="run-a", run_seq=1)
    with pytest.raises(
        sqlite3.IntegrityError,
        match=r"UNIQUE constraint failed: events\.run_id, events\.run_seq",
    ):
        _raw_insert(conn, project_id, run_id="run-a", run_seq=1)


def test_reopening_keeps_one_table_two_indexes_two_triggers(repo):
    store_db.open_db(repo).close()
    conn = store_db.open_db(repo)
    try:
        objects = _events_objects(conn)
    finally:
        conn.close()
    assert objects == _EVENTS_OBJECTS


def _write_pre_events_db(repo_dir: str) -> None:
    """An `am.db` as a build before the `events` table wrote it: `_SCHEMA` up
    to the `events` DDL, stamped `SCHEMA_VERSION`, with one `projects` row."""
    path = paths.db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    pre_events = store_db._SCHEMA[
        : store_db._SCHEMA.index("CREATE TABLE IF NOT EXISTS events")
    ]
    built = sqlite3.connect(path)
    try:
        built.executescript(pre_events)
        built.execute(
            "INSERT INTO projects (repo_dir, created_at) VALUES (?, ?)", (repo_dir, TS)
        )
        built.execute(f"PRAGMA user_version = {store_db.SCHEMA_VERSION}")
        built.commit()
        names = {row[0] for row in built.execute("SELECT name FROM sqlite_master")}
    finally:
        built.close()
    assert "events" not in names


def test_an_am_db_without_events_gains_it_on_open(repo):
    _write_pre_events_db("/kept")

    conn = store_db.open_db(repo)
    try:
        objects = _events_objects(conn)
        kept = [row["repo_dir"] for row in conn.execute("SELECT repo_dir FROM projects")]
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()

    assert objects == _EVENTS_OBJECTS
    assert kept == ["/kept"]
    assert version == store_db.SCHEMA_VERSION


def test_open_db_for_reading_upgrades_an_am_db_without_events(repo):
    # Review Focus 1: a read-only command on a pre-card file falls through to
    # `open_db`, which creates `events`; the existing rows still read.
    _write_pre_events_db("/kept")

    conn = store_db.open_db_for_reading(repo)
    try:
        objects = _events_objects(conn)
        kept = [row["repo_dir"] for row in conn.execute("SELECT repo_dir FROM projects")]
    finally:
        conn.close()

    assert objects == _EVENTS_OBJECTS
    assert kept == ["/kept"]
