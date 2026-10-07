"""`agent_manager.migrate`: merging every per-project database an older `am`
left into `am.db`. Legacy files are built with stdlib `sqlite3` under the
test's data directory; nothing spawns a process, so these are unit tests.
"""

import ast
import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest
from legacyhelpers import (
    LEGACY_TABLES,
    NOW,
    OLD_SCHEMA,
    STAMP,
    full_rows,
    project_file,
    projects_dir,
    run_row,
    tree,
    write_db,
    write_wal_db,
)

from agent_manager import migrate, models, paths
from agent_manager.store import db as store_db
from agent_manager.store import queries as store_queries

HOST = "here"


def _dead(pid: int) -> bool:
    return False


def _alive(pid: int) -> bool:
    return True


def _run(*, alive=_dead, now=NOW, host=HOST) -> migrate.MigrationReport:
    return migrate.migrate(now=now, host=host, alive=alive)


def _query(sql: str, params: tuple[object, ...] = ()) -> list[sqlite3.Row]:
    conn = sqlite3.connect(paths.db_path())
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _counts() -> dict[str, int]:
    return {
        table: _query(f"SELECT COUNT(*) AS n FROM {table}")[0]["n"]
        for table in (*LEGACY_TABLES, "projects")
    }


def _marker() -> list[str]:
    return [
        row["value"]
        for row in _query("SELECT value FROM meta WHERE key = ?", (store_db.MIGRATED_KEY,))
    ]


@pytest.fixture
def repos(tmp_path) -> tuple[Path, Path]:
    alpha, beta = tmp_path / "alpha", tmp_path / "beta"
    alpha.mkdir()
    beta.mkdir()
    return alpha, beta


def test_migrate_imports_no_sqlite3():
    tree_ = ast.parse(Path(migrate.__file__).read_text())
    imported = {
        alias.name for node in ast.walk(tree_) if isinstance(node, ast.Import)
        for alias in node.names
    } | {node.module for node in ast.walk(tree_) if isinstance(node, ast.ImportFrom)}
    assert "sqlite3" not in imported


def test_no_legacy_files_returns_nothing_and_creates_nothing():
    report = _run()

    assert report == migrate.MigrationReport(
        already_migrated=False, migrated_at=None, projects=(), skipped=()
    )
    assert not paths.data_path().exists()


def test_two_legacy_files_merge_into_two_projects_with_every_row(repos):
    alpha, beta = repos
    write_db(project_file(alpha), full_rows("run-a", alpha))
    write_db(project_file(beta), full_rows("run-b", beta))

    report = _run()

    assert report.already_migrated is False
    assert report.migrated_at == store_db.iso(NOW)
    assert report.skipped == ()
    assert [project.path for project in report.projects] == paths.legacy_project_dbs()
    ids = {row["repo_dir"]: row["id"] for row in _query("SELECT id, repo_dir FROM projects")}
    by_repo = {project.repo_dir: project for project in report.projects}
    assert set(by_repo) == set(ids) == {str(alpha.resolve()), str(beta.resolve())}
    for repo, run_id in ((alpha, "run-a"), (beta, "run-b")):
        project = by_repo[str(repo.resolve())]
        assert project.project_id == ids[str(repo.resolve())]
        assert project.rows == {table: 1 for table in LEGACY_TABLES}
        assert project.ignored_tables == ()
        for table in LEGACY_TABLES:
            column = "id" if table == "runs" else "run_id"
            found = _query(f"SELECT project_id FROM {table} WHERE {column} = ?", (run_id,))
            assert [row["project_id"] for row in found] == [project.project_id], table
    assert _marker() == [store_db.iso(NOW)]


def test_an_old_schema_file_reads_missing_columns_as_null_and_drops_retired_ones(repos):
    alpha, _ = repos
    rows = full_rows("run-a", alpha)
    rows["attempts"] = [{**rows["attempts"][0], "tokens_in": 10, "tokens_out": 20, "cost": 0.5}]
    write_db(project_file(alpha), rows, schema=OLD_SCHEMA)

    (project,) = _run().projects

    assert project.rows["attempts"] == 1
    assert _query("SELECT milestone_id FROM runs")[0]["milestone_id"] is None
    assert _query("SELECT detail FROM phases")[0]["detail"] is None
    columns = {row["name"] for row in _query("PRAGMA table_info(attempts)")}
    assert not {"tokens_in", "tokens_out", "cost"} & columns


def test_a_dev_build_file_has_its_project_ids_remapped_and_projects_ignored(repos):
    alpha, _ = repos
    rows = {
        table: [{"project_id": 99, **row} for row in table_rows]
        for table, table_rows in full_rows("run-a", alpha).items()
    }
    rows["projects"] = [{"id": 99, "repo_dir": str(alpha.resolve()), "created_at": STAMP}]
    write_db(project_file(alpha), rows, schema=store_db._SCHEMA)

    (project,) = _run().projects

    assert project.project_id != 99
    assert [row["id"] for row in _query("SELECT id FROM projects")] == [project.project_id]
    for table in LEGACY_TABLES:
        found = {row["project_id"] for row in _query(f"SELECT project_id FROM {table}")}
        assert found == {project.project_id}, table
    assert "projects" in project.ignored_tables
    assert "meta" in project.ignored_tables


def test_files_without_runs_are_skipped_and_the_marker_is_still_written(repos, tmp_path):
    alpha, beta = repos
    write_db(project_file(alpha))
    project_file(beta).write_bytes(b"")
    write_db(project_file(tmp_path / "gamma"), schema="CREATE TABLE notes (x);")

    report = _run()

    assert report.projects == ()
    assert report.skipped == tuple(paths.legacy_project_dbs())
    assert len(report.skipped) == 3
    assert report.migrated_at == store_db.iso(NOW)
    assert _marker() == [store_db.iso(NOW)]


def test_a_skipped_file_beside_a_merged_one_is_reported_as_skipped(repos):
    alpha, beta = repos
    merged = write_db(project_file(alpha), full_rows("run-a", alpha))
    empty = write_db(project_file(beta))

    report = _run()

    assert [project.path for project in report.projects] == [merged]
    assert report.skipped == (empty,)


def test_a_second_call_returns_the_first_marker_and_opens_no_legacy_file(repos):
    alpha, _ = repos
    write_db(project_file(alpha), full_rows("run-a", alpha))
    first = _run()
    counts = _counts()
    am_bytes = paths.db_path().read_bytes()
    project_file(alpha).write_bytes(b"not a database " * 200)

    second = _run(now=NOW + timedelta(hours=1))

    assert second == migrate.MigrationReport(
        already_migrated=True, migrated_at=first.migrated_at, projects=(), skipped=()
    )
    assert _counts() == counts
    assert paths.db_path().read_bytes() == am_bytes


def test_a_racing_second_call_sees_the_marker_inside_its_transaction(repos, monkeypatch):
    alpha, beta = repos
    write_db(project_file(alpha), full_rows("run-a", alpha))
    write_db(project_file(beta), full_rows("run-b", beta))
    first = _run()
    counts = _counts()
    monkeypatch.setattr(store_db, "migration_marker", lambda location: None)

    second = _run(now=NOW + timedelta(hours=1))

    assert second.already_migrated is True
    assert second.migrated_at == first.migrated_at
    assert second.projects == ()
    assert _counts() == counts


def test_leftover_wal_rows_migrate_and_every_legacy_file_keeps_its_bytes(
    repos, tmp_path, monkeypatch
):
    alpha, _ = repos
    rows = full_rows("run-a", alpha)
    write_wal_db(
        project_file(alpha),
        tmp_path / "build",
        {"runs": [run_row("run-in-wal", alpha)]},
        rows=rows,
    )
    (projects_dir() / f"{paths.project_digest(alpha)}.events.lock").write_bytes(b"")
    before = tree(projects_dir())
    assert {name for name in before if name.endswith(("-wal", "-shm", ".lock"))}
    opened: list[str] = []
    real_connect = sqlite3.connect

    def spy(database, *args, **kwargs):
        opened.append(str(database))
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", spy)

    (project,) = _run().projects
    after_first = tree(projects_dir())
    _run()
    by_migrate = list(opened)

    assert project.rows["runs"] == 2
    assert {row["id"] for row in _query("SELECT id FROM runs")} == {"run-a", "run-in-wal"}
    assert after_first == before
    assert tree(projects_dir()) == before
    assert by_migrate
    assert not [entry for entry in by_migrate if str(projects_dir()) in entry]


def test_a_legacy_cancel_lists_canonically_and_is_stored_verbatim(repos):
    alpha, _ = repos
    rows = full_rows("run-a", alpha)
    rows["runs"] = [run_row("run-a", alpha, status=models.LEGACY_CANCELED)]
    write_db(project_file(alpha), rows)

    (project,) = _run().projects

    conn = store_db.open_db_for_reading(alpha)
    try:
        (summary,) = store_queries.list_runs(conn, project_id=project.project_id)
    finally:
        conn.close()
    assert summary.status == models.CANCELED
    assert _query("SELECT status FROM runs")[0]["status"] == models.LEGACY_CANCELED


def test_after_a_merge_the_store_opens_again(repos):
    alpha, _ = repos
    write_db(project_file(alpha), full_rows("run-a", alpha))
    with pytest.raises(store_db.MigrationRequiredError):
        store_db.open_db(alpha)

    _run()

    store_db.open_db(alpha).close()
