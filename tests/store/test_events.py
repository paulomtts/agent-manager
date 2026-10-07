"""Behaviour of the `events` table and of `agent_manager.store.events`: the
append-only rows, their DDL in `store.db`, and where the module sits.

Real SQLite files under `tmp_path` through the `repo` and `conns` fixtures of
`tests/store/conftest.py`; nothing spawns a process, so these are unit tests.
"""

import ast
import inspect
import json
import sqlite3
import sys
import types
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from agent_manager import paths, store
from agent_manager.store import db as store_db
from agent_manager.store import events as store_events
from agent_manager.store import journal as store_journal

TS = "2026-10-07T12:00:00+00:00"


def _insert(
    conn: sqlite3.Connection, project_id: int, **overrides
) -> store_events.EventRow:
    """`store_events.insert` with every required field defaulted; does not commit."""
    fields = {
        "project_id": project_id,
        "run_id": "run-a",
        "ts": TS,
        "kind": "phase_started",
        "payload": {"n": 1},
        "source": "live",
    }
    fields.update(overrides)
    return store_events.insert(conn, **fields)


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


# ── store.events: where the module sits ──────────────────────────────────────


def test_events_is_a_leaf_module_of_the_store_package():
    for function in (
        store_events.insert,
        store_events.read,
        store_events.head,
        store_events.run_lines,
        store_events.journal_line,
    ):
        assert function.__module__ == "agent_manager.store.events"
    assert store_events.EventRow.__module__ == "agent_manager.store.events"
    assert inspect.ismodule(store.events)
    assert store.events is store_events
    for name in ("insert", "read", "head", "run_lines", "journal_line", "EventRow"):
        assert not hasattr(store, name)


_EVENTS_MAY_IMPORT_FROM_THE_STORE = frozenset(
    {("db", "store_db"), ("journal", "store_journal")}
)
"""`(name, asname)` of the `from agent_manager.store import ...` aliases
`store/events.py` may use: both are lower layers (§4.1)."""


def _outside_imports(source: str) -> list[str]:
    """Every import in `source` that is neither the stdlib nor one of
    `_EVENTS_MAY_IMPORT_FROM_THE_STORE`."""
    outside: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            outside.extend(
                alias.name
                for alias in node.names
                if alias.name.split(".")[0] not in sys.stdlib_module_names
            )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                outside.append("." * node.level + module)
            elif module == "agent_manager.store":
                outside.extend(
                    f"agent_manager.store.{alias.name}"
                    for alias in node.names
                    if (alias.name, alias.asname) not in _EVENTS_MAY_IMPORT_FROM_THE_STORE
                )
            elif module.split(".")[0] not in sys.stdlib_module_names:
                outside.append(module)
    return outside


def test_events_imports_only_the_stdlib_store_db_and_store_journal():
    assert _outside_imports(Path(store_events.__file__).read_text()) == []
    # The guard still refuses everything else.
    assert _outside_imports("from agent_manager.store import replay as store_replay") == [
        "agent_manager.store.replay"
    ]
    assert _outside_imports("from agent_manager import models") == ["agent_manager"]
    assert _outside_imports("import pydantic") == ["pydantic"]


_TRANSACTION_CALLS = frozenset({"commit", "rollback", "immediate", "open_db", "connect"})


def test_events_never_commits_or_opens_a_transaction():
    found: list[str] = []
    for node in ast.walk(ast.parse(Path(store_events.__file__).read_text())):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in _TRANSACTION_CALLS
        ):
            found.append(f"line {node.lineno}: .{node.func.attr}()")
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and "BEGIN" in node.value
        ):
            found.append(f"line {node.lineno}: {node.value!r}")
    assert found == []


# ── insert ───────────────────────────────────────────────────────────────────


def test_insert_returns_the_stored_row(conns, project_id):
    conn, _ = conns

    minimal = _insert(conn, project_id)
    full = store_events.insert(
        conn,
        project_id=project_id,
        run_id="run-a",
        ts=TS,
        kind="attempt_finished",
        payload={"exit_code": 0},
        source="imported",
        story_id="story-1",
        card_id="card-1",
        phase="plan",
        attempt=2,
        schema=3,
    )

    assert minimal == store_events.EventRow(
        seq=minimal.seq,
        project_id=project_id,
        run_id="run-a",
        run_seq=1,
        ts=TS,
        kind="phase_started",
        story_id=None,
        card_id=None,
        phase=None,
        attempt=None,
        schema=1,
        payload={"n": 1},
        source="live",
    )
    assert full == store_events.EventRow(
        seq=minimal.seq + 1,
        project_id=project_id,
        run_id="run-a",
        run_seq=2,
        ts=TS,
        kind="attempt_finished",
        story_id="story-1",
        card_id="card-1",
        phase="plan",
        attempt=2,
        schema=3,
        payload={"exit_code": 0},
        source="imported",
    )
    assert store_events.read(conn) == [minimal, full]


def test_insert_stores_ts_verbatim(conns, project_id):
    conn, _ = conns
    stamps = ["2026-10-07T12:00:00.123456+00:00", "2026-10-07T12:00:00Z"]

    for stamp in stamps:
        _insert(conn, project_id, ts=stamp)

    stored = [row["ts"] for row in conn.execute("SELECT ts FROM events ORDER BY seq")]
    assert stored == stamps
    assert [row.ts for row in store_events.read(conn)] == stamps


def test_insert_stores_payload_with_sorted_keys(conns, project_id):
    conn, _ = conns
    payload = {"zeta": 1, "alpha": {"y": 2, "b": 3}, "mid": [3, 1]}

    row = _insert(conn, project_id, payload=payload)

    stored = conn.execute(
        "SELECT payload FROM events WHERE seq = ?", (row.seq,)
    ).fetchone()[0]
    assert stored == json.dumps(payload, sort_keys=True)
    assert stored.index('"alpha"') < stored.index('"mid"') < stored.index('"zeta"')
    assert stored.index('"b"') < stored.index('"y"')
    assert row.payload == payload


def test_insert_accepts_any_mapping_payload(conns, project_id):
    # Review Focus 2: the parameter is a Mapping, not only a dict.
    conn, _ = conns

    row = _insert(conn, project_id, payload=types.MappingProxyType({"b": 1, "a": 2}))

    assert row.payload == {"a": 2, "b": 1}
    assert conn.execute(
        "SELECT payload FROM events WHERE seq = ?", (row.seq,)
    ).fetchone()[0] == '{"a": 2, "b": 1}'


def test_payload_round_trips_unicode_and_nesting(conns, project_id):
    # Review Focus 3.
    conn, _ = conns
    payload = {"title": "café ✓ 事件", "items": [{"k": None}, [1, 2.5, True]]}

    row = _insert(conn, project_id, payload=payload)

    assert row.payload == payload
    assert store_events.read(conn)[0].payload == payload


def test_run_seq_counts_per_run_from_one(conns, project_id, other_project_id):
    conn, _ = conns

    order = [
        ("run-a", project_id),
        ("run-b", other_project_id),
        ("run-a", project_id),
        ("run-b", other_project_id),
        ("run-a", project_id),
    ]
    rows = [_insert(conn, project, run_id=run) for run, project in order]

    assert [(row.run_id, row.run_seq) for row in rows] == [
        ("run-a", 1),
        ("run-b", 1),
        ("run-a", 2),
        ("run-b", 2),
        ("run-a", 3),
    ]


def test_explicit_run_seq_is_kept(conns, project_id):
    conn, _ = conns

    kept = _insert(conn, project_id, run_seq=7)
    following = _insert(conn, project_id)

    assert kept.run_seq == 7
    assert following.run_seq == 8


def test_duplicate_run_seq_is_refused(conns, project_id):
    conn, _ = conns
    _insert(conn, project_id, run_id="run-a", run_seq=4)

    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed"):
        _insert(conn, project_id, run_id="run-a", run_seq=4)
    elsewhere = _insert(conn, project_id, run_id="run-b", run_seq=4)

    assert elsewhere.run_seq == 4
    assert [(row.run_id, row.run_seq) for row in store_events.read(conn)] == [
        ("run-a", 4),
        ("run-b", 4),
    ]


@pytest.mark.parametrize("field", ["project_id", "run_id", "ts", "kind"])
def test_a_missing_required_value_is_refused(conns, project_id, field):
    conn, _ = conns
    fields = {
        "project_id": project_id,
        "run_id": "run-a",
        "ts": TS,
        "kind": "phase_started",
        "payload": {"n": 1},
        "source": "live",
        field: None,
    }
    with pytest.raises(sqlite3.IntegrityError, match="NOT NULL constraint failed"):
        store_events.insert(conn, **fields)
    conn.rollback()
    assert store_events.head(conn) == 0


def test_source_outside_live_and_imported_is_refused_by_insert(conns, project_id):
    conn, _ = conns
    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
        _insert(conn, project_id, source="replayed")


def test_seq_is_strictly_increasing_across_runs_and_projects(
    conns, project_id, other_project_id
):
    conn, _ = conns
    order = [
        ("run-a", project_id),
        ("run-c", other_project_id),
        ("run-b", project_id),
        ("run-c", other_project_id),
        ("run-a", project_id),
    ]

    seqs = [_insert(conn, project, run_id=run).seq for run, project in order]

    assert all(earlier < later for earlier, later in zip(seqs, seqs[1:]))


def test_insert_does_not_commit(conns, project_id):
    conn, other = conns

    row = _insert(conn, project_id)

    assert conn.in_transaction
    assert store_events.head(other) == 0
    assert store_events.read(other) == []
    conn.commit()
    assert store_events.head(other) == row.seq
    assert store_events.read(other) == [row]


def test_a_rolled_back_insert_consumes_no_seq(conns, project_id):
    conn, _ = conns
    first = _insert(conn, project_id)
    conn.commit()
    _insert(conn, project_id)
    conn.rollback()

    second = _insert(conn, project_id)
    conn.commit()

    assert second.seq == first.seq + 1
    assert second.run_seq == first.run_seq + 1
    assert conn.execute(
        "SELECT seq FROM sqlite_sequence WHERE name = 'events'"
    ).fetchone()[0] == second.seq


def test_unserializable_payload_inserts_nothing(conns, project_id):
    conn, _ = conns
    _insert(conn, project_id)
    conn.commit()
    before = store_events.head(conn)

    with pytest.raises(TypeError):
        _insert(conn, project_id, payload={"x": object()})

    assert not conn.in_transaction
    assert store_events.head(conn) == before


# ── head ─────────────────────────────────────────────────────────────────────


def test_head_of_an_empty_table_is_zero(conns):
    conn, _ = conns
    assert store_events.head(conn) == 0


def test_head_is_the_largest_seq(conns, project_id, other_project_id):
    conn, _ = conns
    _insert(conn, project_id, run_id="run-a")
    last = _insert(conn, other_project_id, run_id="run-b")

    assert store_events.head(conn) == last.seq


# ── run_ids ──────────────────────────────────────────────────────────────────


def test_run_ids_of_an_empty_table_is_empty(conns):
    conn, _ = conns
    assert store_events.run_ids(conn) == []


def test_run_ids_lists_each_run_once_sorted_over_every_kind_and_project(
    conns, project_id, other_project_id
):
    conn, _ = conns
    _insert(conn, project_id, run_id="run-c", kind="phase_upsert")
    _insert(conn, other_project_id, run_id="run-a", kind="run_upsert")
    _insert(conn, project_id, run_id="run-b", kind="lease_acquired")
    _insert(conn, project_id, run_id="run-c", kind="attempt_upsert")
    conn.commit()

    assert store_events.run_ids(conn) == ["run-a", "run-b", "run-c"]
    assert not conn.in_transaction


# ── read ─────────────────────────────────────────────────────────────────────


def _five_rows(conn, project_id, other_project_id) -> list[store_events.EventRow]:
    order = [
        ("run-a", project_id),
        ("run-b", project_id),
        ("run-c", other_project_id),
        ("run-a", project_id),
        ("run-c", other_project_id),
    ]
    rows = [_insert(conn, project, run_id=run) for run, project in order]
    conn.commit()
    return rows


def test_read_returns_rows_after_seq_in_order(conns, project_id, other_project_id):
    conn, _ = conns
    rows = _five_rows(conn, project_id, other_project_id)

    assert store_events.read(conn) == rows
    assert store_events.read(conn, after_seq=rows[1].seq) == rows[2:]
    assert store_events.read(conn, after_seq=rows[-1].seq) == []
    assert store_events.read(conn, after_seq=rows[-1].seq + 10) == []


def test_read_with_a_negative_after_seq_reads_everything(
    conns, project_id, other_project_id
):
    # Review Focus 5.
    conn, _ = conns
    rows = _five_rows(conn, project_id, other_project_id)

    assert store_events.read(conn, after_seq=-5) == rows


def test_read_limit_pages_without_gap_or_repeat(conns, project_id, other_project_id):
    conn, _ = conns
    rows = _five_rows(conn, project_id, other_project_id)

    paged: list[store_events.EventRow] = []
    after = 0
    while page := store_events.read(conn, after_seq=after, limit=2):
        assert len(page) <= 2
        paged.extend(page)
        after = page[-1].seq

    assert paged == rows


def test_read_filters_by_run_and_by_project(conns, project_id, other_project_id):
    conn, _ = conns
    rows = _five_rows(conn, project_id, other_project_id)

    assert store_events.read(conn, run_id="run-a") == [rows[0], rows[3]]
    assert store_events.read(conn, project_id=other_project_id) == [rows[2], rows[4]]
    assert store_events.read(conn, project_id=project_id) == [rows[0], rows[1], rows[3]]
    assert store_events.read(conn, run_id="run-c", project_id=project_id) == []
    assert store_events.read(conn, run_id="run-a", project_id=project_id) == [
        rows[0],
        rows[3],
    ]
    assert store_events.read(
        conn, run_id="run-a", after_seq=rows[0].seq, limit=1
    ) == [rows[3]]


@pytest.mark.parametrize("limit", [0, -1])
def test_read_refuses_a_non_positive_limit(conns, limit):
    conn, _ = conns
    with pytest.raises(ValueError, match="limit"):
        store_events.read(conn, limit=limit)


def test_read_and_head_open_no_transaction(conns, project_id, other_project_id):
    # Review Focus 4.
    conn, _ = conns
    _five_rows(conn, project_id, other_project_id)
    assert not conn.in_transaction

    store_events.read(conn, limit=2)
    store_events.head(conn)

    assert not conn.in_transaction


# ── run_lines and journal_line ───────────────────────────────────────────────

NODE_TS = store_journal.ts_text(datetime(2026, 10, 7, 12, 0, 0, 123456, tzinfo=timezone.utc))
"""A `ts` as the writer stores it, so it round-trips through `ts_text`."""


def test_run_lines_are_the_runs_node_events_ascending_by_run_seq(conns, project_id):
    # Review Focus 3: the two runs' rows interleave in global `seq`.
    conn, _ = conns
    a1 = _insert(conn, project_id, run_id="run-a", kind="run_upsert", ts=NODE_TS, payload={"id": "run-a"})
    _insert(conn, project_id, run_id="run-b", kind="run_upsert", ts=NODE_TS, payload={"id": "run-b"})
    a2 = _insert(
        conn,
        project_id,
        run_id="run-a",
        kind="attempt_upsert",
        ts=NODE_TS,
        payload={"n": 1},
        story_id="s",
        card_id="c",
        phase="implement",
        attempt=1,
    )
    _insert(
        conn, project_id, run_id="run-b", kind="story_upsert", ts=NODE_TS,
        payload={"card_id": "s"}, story_id="s",
    )
    conn.commit()

    lines = store_events.run_lines(conn, "run-a")

    assert lines == [store_events.journal_line(a1), store_events.journal_line(a2)]
    second = lines[1]
    assert (
        second.seq,
        second.run_id,
        second.event,
        second.story,
        second.card,
        second.phase,
        second.attempt,
        second.payload,
    ) == (2, "run-a", "attempt_upsert", "s", "c", "implement", 1, {"n": 1})
    assert store_journal.ts_text(second.ts) == a2.ts
    assert [line.run_id for line in store_events.run_lines(conn, "run-b")] == ["run-b", "run-b"]
    assert [line.seq for line in store_events.run_lines(conn, "run-b")] == [1, 2]


def test_run_lines_order_by_run_seq_not_by_insert_order(conns, project_id):
    conn, _ = conns
    _insert(conn, project_id, kind="story_upsert", ts=NODE_TS, payload={"card_id": "s"}, story_id="s", run_seq=2)
    _insert(conn, project_id, kind="run_upsert", ts=NODE_TS, payload={"id": "run-a"}, run_seq=1)
    conn.commit()

    assert [line.event for line in store_events.run_lines(conn, "run-a")] == [
        "run_upsert",
        "story_upsert",
    ]


def test_run_lines_skip_other_kinds_and_are_empty_for_a_run_without_rows(conns, project_id):
    # Review Focus 2: a lease event may be a run's very first.
    conn, _ = conns
    _insert(conn, project_id, kind="lease_acquired", payload={"token": "t1"})
    kept = _insert(conn, project_id, kind="run_upsert", ts=NODE_TS, payload={"id": "run-a"})
    _insert(conn, project_id, kind="phase_started")
    conn.commit()

    assert store_events.run_lines(conn, "run-a") == [store_events.journal_line(kept)]
    assert store_events.run_lines(conn, "run-never") == []


def _raw_node_row(
    conn: sqlite3.Connection,
    project_id: int,
    *,
    run_seq: int = 1,
    ts: str = NODE_TS,
    payload: str = '{"id": "run-a"}',
) -> None:
    """A `run_upsert` row of `run-a` written by hand, `payload` text verbatim:
    what `store_events.insert`, which serialises, cannot write."""
    conn.execute(
        "INSERT INTO events (project_id, run_id, run_seq, ts, kind, payload, source)"
        " VALUES (?, 'run-a', ?, ?, 'run_upsert', ?, 'live')",
        (project_id, run_seq, ts, payload),
    )


def test_run_lines_name_the_run_and_run_seq_of_a_payload_that_is_not_json(conns, project_id):
    conn, _ = conns
    _raw_node_row(conn, project_id, run_seq=1)
    _raw_node_row(conn, project_id, run_seq=7, payload="{not json")
    conn.commit()

    with pytest.raises(store_journal.JournalError) as caught:
        store_events.run_lines(conn, "run-a")

    message = str(caught.value)
    assert "'run-a'" in message
    assert "run_seq 7" in message
    assert "not JSON" in message
    assert isinstance(caught.value.__cause__, json.JSONDecodeError)


@pytest.mark.parametrize(
    "overrides",
    [{"payload": "[1, 2]"}, {"ts": "not a time"}, {"run_seq": 0}],
    ids=["payload-array", "ts-unparseable", "run-seq-zero"],
)
def test_run_lines_raise_validation_error_for_a_row_no_journal_line_can_hold(
    conns, project_id, overrides
):
    conn, _ = conns
    _raw_node_row(conn, project_id, **overrides)
    conn.commit()

    with pytest.raises(ValidationError):
        store_events.run_lines(conn, "run-a")


def test_run_lines_open_no_transaction(conns, project_id):
    conn, _ = conns
    _insert(conn, project_id, kind="run_upsert", ts=NODE_TS, payload={"id": "run-a"})
    conn.commit()

    store_events.run_lines(conn, "run-a")

    assert not conn.in_transaction
