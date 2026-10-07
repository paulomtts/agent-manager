"""`am migrate`: `migrate.migrate` behind brd's envelope.

Legacy databases and journals are built with `legacyhelpers` under the
per-test data directory, and the command runs in-process through
`CliRunner`; nothing spawns a process, so these are unit tests.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from legacyhelpers import (
    full_rows,
    journal_line,
    lease_row,
    project_file,
    projects_dir,
    tree,
    write_db,
    write_journal,
)
from typer.testing import CliRunner

from agent_manager import cli, migrate, paths
from agent_manager.store import db as store_db

runner = CliRunner()

LONG_AGO = datetime(2020, 1, 1, tzinfo=timezone.utc)
"""A heartbeat stale under the real clock `am migrate` reads.

`legacyhelpers.full_rows` beats at `STAMP`, stale only at the fixed `NOW`
the `migrate` tests pass; the command passes `datetime.now()`, and a lease
of another host whose heartbeat is not yet 30s old is live."""

KEYS = {
    "already_migrated",
    "migrated_at",
    "projects",
    "skipped",
    "journals",
    "missing_journals",
    "orphan_journals",
}

NOTHING = {
    "already_migrated": False,
    "migrated_at": None,
    "projects": [],
    "skipped": [],
    "journals": [],
    "missing_journals": [],
    "orphan_journals": [],
}


def _ts(second: int) -> str:
    return f"2026-10-07T10:00:{second:02d}+00:00"


def _stale_rows(run_id: str, repo: Path) -> dict[str, list[dict[str, object]]]:
    """`full_rows` with its lease beating at `LONG_AGO`."""
    rows = full_rows(run_id, repo)
    rows["run_leases"] = [lease_row(run_id, heartbeat_at=LONG_AGO)]
    return rows


def _live_rows(run_id: str, repo: Path) -> dict[str, list[dict[str, object]]]:
    """`full_rows` with its lease held by another host and beating now."""
    rows = full_rows(run_id, repo)
    rows["run_leases"] = [
        lease_row(run_id, heartbeat_at=datetime.now(timezone.utc), host="elsewhere")
    ]
    return rows


def _migrate(*extra: str):
    return runner.invoke(cli.app, ["migrate", *extra])


def _envelope(result) -> dict:
    return json.loads(result.stdout)


def _listing() -> list[Path]:
    """Every path under the data directory, directories included."""
    return sorted(paths.data_path().rglob("*"))


@pytest.fixture
def repos(tmp_path) -> tuple[Path, Path]:
    alpha, beta = tmp_path / "alpha", tmp_path / "beta"
    alpha.mkdir()
    beta.mkdir()
    return alpha, beta


def test_the_command_does_not_shadow_the_migrate_module():
    import agent_manager.migrate as module

    assert cli.migrate is module


def test_migrate_on_a_machine_with_nothing_returns_null_and_creates_nothing():
    result = _migrate()

    assert result.exit_code == 0, result.output
    assert _envelope(result) == {"ok": True, "data": NOTHING}
    assert not paths.db_path().exists()
    assert not paths.data_path().exists()


def test_an_empty_projects_directory_is_nothing_to_migrate():
    projects_dir()

    result = _migrate()

    assert result.exit_code == 0, result.output
    assert _envelope(result)["data"] == NOTHING
    assert _listing() == [paths.data_path() / "projects"]


def test_a_machine_already_on_am_db_with_no_legacy_file_is_left_alone(repos):
    alpha, _ = repos
    store_db.open_db(alpha.resolve()).close()
    before = tree(paths.data_path())
    listing = _listing()

    result = _migrate()

    assert result.exit_code == 0, result.output
    assert _envelope(result)["data"] == NOTHING
    assert tree(paths.data_path()) == before
    assert _listing() == listing


def test_migrate_reports_counts_skipped_dbs_and_torn_lines(repos):
    alpha, beta = repos
    merged = write_db(project_file(alpha), _stale_rows("run-a", alpha))
    empty = write_db(project_file(beta))
    journal = write_journal(
        "run-a",
        [journal_line("run-a", 1, _ts(1)), journal_line("run-a", 2, _ts(2))],
        tail='{"seq": 3, "ts',
    )

    result = _migrate()

    assert result.exit_code == 0, result.output
    envelope = _envelope(result)
    assert envelope["ok"] is True
    data = envelope["data"]
    assert set(data) == KEYS
    assert data["already_migrated"] is False
    assert isinstance(data["migrated_at"], str)
    (project,) = data["projects"]
    assert set(project) == {"path", "repo_dir", "project_id", "rows", "ignored_tables"}
    assert project["path"] == str(merged)
    assert project["repo_dir"] == str(alpha.resolve())
    assert isinstance(project["project_id"], int)
    assert project["rows"]["runs"] == 1
    assert data["skipped"] == [str(empty)]
    assert data["journals"] == [
        {"run_id": "run-a", "path": str(journal), "events": 2, "torn_line": 3}
    ]
    assert data["missing_journals"] == []
    assert data["orphan_journals"] == []


def test_migrate_leaves_every_legacy_file_and_journal_untouched(repos):
    alpha, _ = repos
    write_db(project_file(alpha), _stale_rows("run-a", alpha))
    write_journal("run-a", [journal_line("run-a", 1, _ts(1))])
    write_journal("run-orphan", [journal_line("run-orphan", 1, _ts(1))])
    legacy_before = tree(projects_dir())
    runs_before = tree(paths.data_path() / "runs")

    result = _migrate()

    assert result.exit_code == 0, result.output
    assert tree(projects_dir()) == legacy_before
    assert tree(paths.data_path() / "runs") == runs_before


def test_pretty_indents_the_envelope_and_compact_is_one_line():
    compact = _migrate()
    pretty = _migrate("--pretty")

    assert compact.exit_code == 0, compact.output
    assert pretty.exit_code == 0, pretty.output
    assert compact.stdout.count("\n") == 1
    assert pretty.stdout.count("\n") > 1
    assert '\n  "data": {\n' in pretty.stdout
    assert json.loads(compact.stdout) == json.loads(pretty.stdout)


def test_a_second_migrate_is_a_no_op(repos):
    alpha, _ = repos
    write_db(project_file(alpha), _stale_rows("run-a", alpha))
    write_journal("run-a", [journal_line("run-a", 1, _ts(1))])
    first = _migrate()
    assert first.exit_code == 0, first.output
    migrated_at = _envelope(first)["data"]["migrated_at"]
    before = tree(paths.data_path())
    listing = _listing()

    result = _migrate()

    assert result.exit_code == 0, result.output
    assert _envelope(result)["data"] == {
        **NOTHING,
        "already_migrated": True,
        "migrated_at": migrated_at,
    }
    assert tree(paths.data_path()) == before
    assert _listing() == listing


def test_migrate_works_where_other_commands_require_it(repos):
    alpha, _ = repos
    write_db(project_file(alpha), _stale_rows("run-a", alpha))

    refused = runner.invoke(cli.app, ["runs", "--repo-dir", str(alpha)])
    assert refused.exit_code == cli.EXIT_ERROR, refused.output
    assert _envelope(refused)["error"]["type"] == "MigrationRequiredError"

    migrated = _migrate()
    assert migrated.exit_code == 0, migrated.output
    assert _envelope(migrated)["data"]["already_migrated"] is False

    listed = runner.invoke(cli.app, ["runs", "--repo-dir", str(alpha)])
    assert listed.exit_code == 0, listed.output
    assert [run["id"] for run in _envelope(listed)["data"]["runs"]] == ["run-a"]


def test_migrate_takes_no_repo_dir(repos):
    alpha, _ = repos
    write_db(project_file(alpha), _stale_rows("run-a", alpha))
    before = tree(paths.data_path())

    result = _migrate("--repo-dir", str(alpha))

    assert result.exit_code == 2, result.output
    assert not paths.db_path().exists()
    assert tree(paths.data_path()) == before
