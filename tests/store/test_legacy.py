"""`agent_manager.store.legacy`: reading a per-project database from an older
`am` through a private copy. Stdlib `sqlite3` on files under tmp_path; no
process is spawned, so these are unit tests.
"""

import json
import sqlite3
import tempfile
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import pytest
from legacyhelpers import (
    LEGACY_TABLES,
    NOW,
    OLD_SCHEMA,
    STAMP,
    full_rows,
    journal_line,
    lease_row,
    run_row,
    tree,
    write_db,
    write_journal,
    write_wal_db,
)

from agent_manager import paths
from agent_manager.store import db as store_db
from agent_manager.store import events as store_events
from agent_manager.store import journal as store_journal
from agent_manager.store import leases as store_leases
from agent_manager.store import legacy as store_legacy
from agent_manager.store import projects as store_projects


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


@pytest.fixture
def am() -> Iterator[sqlite3.Connection]:
    """A connection on the test's `am.db`, opened as `migrate` opens it."""
    conn = store_db.open_db_for_migration()
    try:
        yield conn
    finally:
        conn.close()


def _observe(sql: str, params: tuple[object, ...] = ()) -> list[sqlite3.Row]:
    """`sql` on a fresh connection: only what has been committed."""
    conn = sqlite3.connect(paths.db_path())
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _legacy(tmp_path: Path, name: str, rows, *, schema: str | None = None):
    kwargs = {} if schema is None else {"schema": schema}
    return store_legacy.read_legacy(write_db(tmp_path / "legacy" / name, rows, **kwargs))


def test_merge_copies_every_table_under_the_new_project_and_writes_the_marker(
    tmp_path, am
):
    alpha, beta = tmp_path / "alpha", tmp_path / "beta"
    first = _legacy(tmp_path, "a.db", full_rows("run-a", alpha))
    second = _legacy(tmp_path, "b.db", full_rows("run-b", beta))

    outcome = store_legacy.merge(am, [first, second], now=NOW)

    assert outcome.already_migrated is False
    assert outcome.migrated_at == store_db.iso(NOW)
    assert [merged.path for merged in outcome.merged] == [first.path, second.path]
    ids = {row["repo_dir"]: row["id"] for row in _observe("SELECT id, repo_dir FROM projects")}
    assert ids == {
        str(alpha.resolve()): outcome.merged[0].project_id,
        str(beta.resolve()): outcome.merged[1].project_id,
    }
    for merged, run_id in zip(outcome.merged, ("run-a", "run-b"), strict=True):
        assert merged.repo_dir in ids
        assert merged.rows == {table: 1 for table in LEGACY_TABLES}
        assert merged.ignored_tables == ()
        for table in LEGACY_TABLES:
            column = "id" if table == "runs" else "run_id"
            found = _observe(f"SELECT project_id FROM {table} WHERE {column} = ?", (run_id,))
            assert [row["project_id"] for row in found] == [merged.project_id], table
    marker = _observe("SELECT value FROM meta WHERE key = ?", (store_db.MIGRATED_KEY,))
    assert [row["value"] for row in marker] == [store_db.iso(NOW)]


_SPARSE = """
CREATE TABLE runs (
    id TEXT PRIMARY KEY, workflow TEXT, repo_dir TEXT, base_branch TEXT,
    branch_prefix TEXT, status TEXT, started_at TEXT, config TEXT,
    project_id INTEGER, tokens_in INTEGER
);
CREATE TABLE board_comments (
    run_id TEXT, card_id TEXT, key TEXT PRIMARY KEY, body TEXT, state TEXT,
    comment_id TEXT, created_at TEXT, posted_at TEXT
);
CREATE TABLE notes (x);
"""
"""A legacy file whose columns differ from `am.db`'s both ways."""


def test_merge_matches_columns_by_name(tmp_path, am):
    repo = tmp_path / "alpha"
    rows = {
        "runs": [{**run_row("run-a", repo), "project_id": 99, "tokens_in": 7}],
        "board_comments": [
            {"run_id": "run-a", "card_id": "c1", "key": "k1", "body": "b",
             "state": "pending", "created_at": STAMP},
        ],
        "notes": [{"x": 1}],
    }
    legacy = _legacy(tmp_path, "a.db", rows, schema=_SPARSE)

    (merged,) = store_legacy.merge(am, [legacy], now=NOW).merged

    run = _observe("SELECT * FROM runs")[0]
    assert run["project_id"] == merged.project_id != 99
    assert run["milestone_id"] is None
    assert "tokens_in" not in run.keys()
    comment = _observe("SELECT failed_attempts, project_id FROM board_comments")[0]
    assert comment["failed_attempts"] == 0
    assert comment["project_id"] == merged.project_id
    assert merged.ignored_tables == ("notes",)
    assert merged.rows == {table: 0 for table in LEGACY_TABLES} | {
        "runs": 1,
        "board_comments": 1,
    }


def test_merge_reads_an_old_schema_by_name(tmp_path, am):
    repo = tmp_path / "alpha"
    rows = full_rows("run-a", repo)
    rows["attempts"] = [{**rows["attempts"][0], "tokens_in": 1, "tokens_out": 2, "cost": 0.5}]
    legacy = _legacy(tmp_path, "a.db", rows, schema=OLD_SCHEMA)

    (merged,) = store_legacy.merge(am, [legacy], now=NOW).merged

    assert merged.rows["attempts"] == 1
    assert _observe("SELECT detail FROM phases")[0]["detail"] is None
    assert _observe("SELECT milestone_id FROM runs")[0]["milestone_id"] is None


def test_merge_adopts_an_existing_project_row_with_its_created_at(tmp_path, am):
    repo = tmp_path / "alpha"
    earlier = datetime.fromisoformat(STAMP)
    with store_db.immediate(am):
        existing = store_projects.resolve(am, repo, now=earlier)
    legacy = _legacy(tmp_path, "a.db", full_rows("run-a", repo))

    (merged,) = store_legacy.merge(am, [legacy], now=NOW).merged

    assert merged.project_id == existing
    assert [row["created_at"] for row in _observe("SELECT created_at FROM projects")] == [
        STAMP
    ]


def test_merge_returns_the_marker_already_there_and_writes_nothing(tmp_path, am):
    with store_db.immediate(am):
        am.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?)", (store_db.MIGRATED_KEY, STAMP)
        )
    legacy = _legacy(tmp_path, "a.db", full_rows("run-a", tmp_path / "alpha"))

    outcome = store_legacy.merge(am, [legacy], now=NOW)

    assert outcome == store_legacy.MergeOutcome(
        migrated_at=STAMP, already_migrated=True, merged=()
    )
    assert _observe("SELECT * FROM projects") == []
    assert _observe("SELECT * FROM runs") == []


def test_merge_refuses_a_run_id_already_in_am_db_and_commits_nothing(tmp_path, am):
    other = tmp_path / "other"
    with store_db.immediate(am):
        project_id = store_projects.resolve(am, other, now=NOW)
        am.execute(
            "INSERT INTO runs (project_id, id, workflow, repo_dir, base_branch,"
            " branch_prefix, status, config) VALUES (?, 'run-a', 'task', ?, 'main',"
            " 'am/', 'done', '{}')",
            (project_id, str(other.resolve())),
        )
    legacy = _legacy(tmp_path, "a.db", full_rows("run-a", tmp_path / "alpha"))

    with pytest.raises(store_legacy.LegacyRunClashError) as raised:
        store_legacy.merge(am, [legacy], now=NOW)

    assert raised.value.run_id == "run-a"
    assert raised.value.path == legacy.path
    assert len(_observe("SELECT * FROM projects")) == 1
    assert _observe("SELECT * FROM meta WHERE key = ?", (store_db.MIGRATED_KEY,)) == []


def test_merge_reports_a_clashing_row_and_rolls_everything_back(tmp_path, am):
    orphan = {"run_id": "ghost", "card_id": "s9", "title": "t", "level": 0,
              "status": "done", "position": 0}
    first_rows = full_rows("run-a", tmp_path / "alpha")
    first_rows["stories"].append(orphan)
    second_rows = full_rows("run-b", tmp_path / "beta")
    second_rows["stories"].append(orphan)
    first = _legacy(tmp_path, "a.db", first_rows)
    second = _legacy(tmp_path, "b.db", second_rows)

    with pytest.raises(store_legacy.LegacyRowClashError) as raised:
        store_legacy.merge(am, [first, second], now=NOW)

    assert raised.value.table == "stories"
    assert raised.value.path == second.path
    assert "stories" in str(raised.value)
    assert isinstance(raised.value.__cause__, sqlite3.IntegrityError)
    assert not am.in_transaction
    assert _observe("SELECT * FROM projects") == []
    assert _observe("SELECT * FROM runs") == []
    assert _observe("SELECT * FROM meta WHERE key = ?", (store_db.MIGRATED_KEY,)) == []


# -- import_order: the k-way merge on `ts` (single-store 1.3.2 D5) -----------


def _t(second: int) -> str:
    return f"2026-10-07T12:00:{second:02d}+00:00"


def _verbatim(run_id: str, *stamps: str) -> store_journal.VerbatimJournal:
    """A journal of `run_id` read from nowhere: one `run_upsert` line per
    stamp, `run_seq` 1, 2, ... in stamp order."""
    return store_journal.VerbatimJournal(
        run_id=run_id,
        path=Path(f"/nowhere/{run_id}/journal.jsonl"),
        lines=tuple(
            store_journal.VerbatimLine(
                line=n, run_seq=n, ts=ts, kind="run_upsert", story_id=None,
                card_id=None, phase=None, attempt=None, payload={"n": n},
            )
            for n, ts in enumerate(stamps, start=1)
        ),
        torn_line=None,
    )


def _order(journals: list[store_journal.VerbatimJournal]) -> list[tuple[str, int]]:
    return [
        (journal.run_id, line.run_seq)
        for journal, line in store_legacy.import_order(journals)
    ]


def test_import_order_interleaves_by_ts_and_keeps_each_run_in_seq_order():
    a = _verbatim("run-a", _t(1), _t(3))
    b = _verbatim("run-b", _t(2), _t(4))

    assert _order([a, b]) == [("run-a", 1), ("run-b", 1), ("run-a", 2), ("run-b", 2)]


def test_import_order_keeps_a_runs_own_order_when_its_ts_goes_backwards():
    a = _verbatim("run-a", _t(5), _t(1), _t(6))
    b = _verbatim("run-b", _t(2), _t(3))

    assert _order([a, b]) == [
        ("run-b", 1), ("run-b", 2), ("run-a", 1), ("run-a", 2), ("run-a", 3),
    ]


def test_import_order_breaks_ties_by_run_id():
    a = _verbatim("run-a", _t(1))
    b = _verbatim("run-b", _t(1))

    assert _order([b, a]) == [("run-a", 1), ("run-b", 1)]
    assert _order([a, b]) == [("run-a", 1), ("run-b", 1)]


def test_import_order_compares_instants_not_text():
    # As text, "...00.50001+00:00" < "...00.5Z" and "...01.000001+00:00" < "...01Z";
    # as instants both go the other way.
    a = _verbatim("run-a", "2026-10-07T12:00:00.5Z", "2026-10-07T12:00:01Z")
    b = _verbatim(
        "run-b", "2026-10-07T12:00:00.50001+00:00", "2026-10-07T12:00:01.000001+00:00"
    )

    assert _order([a, b]) == [("run-a", 1), ("run-b", 1), ("run-a", 2), ("run-b", 2)]


def test_import_order_of_no_journals_is_empty():
    assert store_legacy.import_order([]) == []
