"""The read-only commands create nothing under the data directory.

`am runs`, `am status`, `am logs`, `am watch` and `am run --dry-run` against a
repository with no projection leave the data directory byte-for-byte as they
found it, and still answer as they do for an unknown run.
"""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agent_manager import cli, models, paths
from agent_manager.store import db as store_db
from agent_manager.store import events as store_events
from agent_manager.store import projects as store_projects
from agent_manager.store import writer as store_writer

runner = CliRunner()

RUN_ID = "20260921T090000Z-cbe34d00"


def _tree(root: Path) -> set[str]:
    """Every path under `root`, relative, directories included."""
    if not root.exists():
        return set()
    return {str(path.relative_to(root)) for path in root.rglob("*")}


def _record(
    root: Path,
    run_id: str = RUN_ID,
    *,
    started_at: datetime = datetime(2026, 9, 21, 9, 0, tzinfo=timezone.utc),
) -> None:
    opened = store_writer.Store.open(root, run_id)
    try:
        opened.record_run(
            models.Run(
                id=run_id,
                workflow="task",
                repo_dir=root,
                base_branch="main",
                branch_prefix="m1",
                status="started",
                started_at=started_at,
            )
        )
    finally:
        opened.close()


@pytest.fixture(params=["fresh", "populated"])
def unknown_repo(request, tmp_path, monkeypatch) -> Path:
    """A repository with no projection, beside a data dir that is empty or holds another project's runs."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    if request.param == "populated":
        other = tmp_path / "other"
        other.mkdir()
        _record(other)
    root = tmp_path / "unknown"
    root.mkdir()
    return root


def _invoke_creating_nothing(tmp_path: Path, argv: list[str]):
    xdg = tmp_path / "xdg"
    before = _tree(xdg)
    existed = xdg.exists()

    result = runner.invoke(cli.app, argv)

    assert _tree(xdg) == before
    assert xdg.exists() == existed
    return result


def test_runs_on_an_unknown_repo_is_empty_and_creates_nothing(unknown_repo, tmp_path):
    result = _invoke_creating_nothing(
        tmp_path, ["runs", "--repo-dir", str(unknown_repo)]
    )

    assert result.exit_code == 0, result.output
    head = 0
    store_id = None
    if paths.db_path().exists():
        conn = store_db.open_db_for_reading(unknown_repo)
        try:
            head = store_events.head(conn)
            store_id = store_db.store_id(conn)
        finally:
            conn.close()
        assert store_id is not None
    assert json.loads(result.stdout) == {
        "ok": True,
        "data": {"runs": [], "as_of_seq": head, "store_id": store_id},
    }


def test_status_of_a_run_on_an_unknown_repo_refuses_and_creates_nothing(
    unknown_repo, tmp_path
):
    result = _invoke_creating_nothing(
        tmp_path, ["status", "nope", "--repo-dir", str(unknown_repo)]
    )

    assert result.exit_code == cli.EXIT_ERROR, result.output
    assert json.loads(result.stdout)["error"] == {
        "type": "UnknownRunError",
        "message": f"run 'nope' is not in the projection for {unknown_repo.resolve()}"
        " (`agent-manager runs` lists the ones that are)",
    }


def test_status_without_a_run_id_on_an_unknown_repo_refuses_and_creates_nothing(
    unknown_repo, tmp_path
):
    result = _invoke_creating_nothing(
        tmp_path, ["status", "--repo-dir", str(unknown_repo)]
    )

    assert result.exit_code == cli.EXIT_ERROR, result.output
    error = json.loads(result.stdout)["error"]
    assert error["type"] == "UnknownRunError"
    assert error["message"].startswith(
        f"no run has been recorded for {unknown_repo.resolve()}"
    )


def _write_stamped_db_without_a_store_id(path: Path) -> None:
    """A current-schema `am.db` at `SCHEMA_VERSION` that no `open_db` has
    touched: no `store_id` row in `meta`, no sidecars."""
    path.parent.mkdir(parents=True, exist_ok=True)
    built = sqlite3.connect(path)
    try:
        built.executescript(store_db._SCHEMA)
        built.execute(f"PRAGMA user_version = {store_db.SCHEMA_VERSION}")
        built.commit()
    finally:
        built.close()


def test_runs_and_status_on_a_database_without_a_store_id_report_null_and_write_nothing(
    tmp_path, monkeypatch
):
    """Review Focus 1 and 3: a reader never back-fills `store_id`."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    root = tmp_path / "repo"
    root.mkdir()
    path = paths.db_path()
    _write_stamped_db_without_a_store_id(path)
    before = path.read_bytes()

    listed = _invoke_creating_nothing(tmp_path, ["runs", "--repo-dir", str(root)])
    shown = _invoke_creating_nothing(tmp_path, ["status", "--repo-dir", str(root)])

    assert listed.exit_code == 0, listed.output
    assert json.loads(listed.stdout) == {
        "ok": True,
        "data": {"runs": [], "as_of_seq": 0, "store_id": None},
    }
    assert shown.exit_code == cli.EXIT_ERROR, shown.output
    assert json.loads(shown.stdout)["error"]["type"] == "UnknownRunError"
    assert "store_id" not in shown.stdout
    assert path.read_bytes() == before


@pytest.mark.parametrize("extra", [[], ["--phase", "verify"], ["--follow"]])
def test_logs_on_an_unknown_repo_refuses_and_creates_nothing(
    unknown_repo, tmp_path, extra
):
    result = _invoke_creating_nothing(
        tmp_path, ["logs", "nope", "card", "--repo-dir", str(unknown_repo), *extra]
    )

    assert result.exit_code == cli.EXIT_ERROR, result.output
    assert "UnknownRunError" in result.output
    assert "run 'nope' is not in the projection" in result.output


def test_watch_of_an_unknown_run_refuses_and_creates_nothing(unknown_repo, tmp_path):
    result = _invoke_creating_nothing(tmp_path, ["watch", "nope"])

    assert result.exit_code == cli.EXIT_ERROR, result.output
    assert json.loads(result.stdout)["error"]["type"] == "UnknownRunError"


def test_watch_all_creates_nothing(unknown_repo, tmp_path):
    result = _invoke_creating_nothing(tmp_path, ["watch", "--all"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["ok"] is True


def test_a_milestone_dry_run_on_an_unknown_repo_creates_nothing(
    unknown_repo, tmp_path, fake_board
):
    milestone = fake_board.add_card("Milestone 1: walking skeleton")
    story = fake_board.add_card("A story", parent_id=milestone)
    fake_board.add_card("A subtask", parent_id=story)

    result = _invoke_creating_nothing(
        tmp_path,
        [
            "run",
            "--milestone",
            "Milestone 1",
            "--dry-run",
            "--repo-dir",
            str(unknown_repo),
            "--base-branch",
            "main",
            "--branch-prefix",
            "m1",
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["ok"] is True


def test_readers_see_a_run_while_a_writer_holds_a_write_transaction(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    root = tmp_path / "live"
    root.mkdir()
    _record(root)
    held = store_db.open_db(root)
    try:
        held.execute("BEGIN IMMEDIATE")
        held.execute("UPDATE runs SET status = 'done'")

        listed = runner.invoke(cli.app, ["runs", "--repo-dir", str(root)])
        shown = runner.invoke(cli.app, ["status", RUN_ID, "--repo-dir", str(root)])
    finally:
        held.rollback()
        held.close()

    assert listed.exit_code == 0, listed.output
    assert [run["id"] for run in json.loads(listed.stdout)["data"]["runs"]] == [RUN_ID]
    assert shown.exit_code == 0, shown.output
    assert json.loads(shown.stdout)["data"]["run"]["status"] == "started"


def test_a_writer_still_creates_the_projection(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    root = tmp_path / "fresh"
    root.mkdir()

    _record(root)

    assert paths.db_path().is_file()
    assert not (paths.data_path() / "projects").exists()


def test_project_run_hides_a_run_another_project_recorded_in_the_same_file(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    theirs = tmp_path / "theirs"
    theirs.mkdir()
    mine = tmp_path / "mine"
    mine.mkdir()
    stranger = tmp_path / "stranger"
    stranger.mkdir()
    _record(theirs)

    # One connection on the file that holds `theirs`'s run, with a second
    # project in it: the file is shared, the run is not.
    conn = store_db.open_db(theirs)
    try:
        store_projects.resolve(conn, mine, now=datetime(2026, 10, 7, tzinfo=timezone.utc))
        conn.commit()
        found = cli._project_run(conn, theirs, RUN_ID)
        assert found is not None and found.id == RUN_ID
        assert cli._project_run(conn, mine, RUN_ID) is None
        assert cli._project_run(conn, stranger, RUN_ID) is None
        assert cli._project_run(conn, theirs, "no-such-run") is None
    finally:
        conn.close()


A_RUN = "20260921T090000Z-aaaaaaaa"
B_RUN = "20260922T090000Z-bbbbbbbb"


@pytest.fixture
def two_repos(tmp_path, monkeypatch) -> tuple[Path, Path]:
    """Repos `a` and `b` sharing one data dir, one run each; `b`'s is newer."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    a = tmp_path / "a"
    a.mkdir()
    b = tmp_path / "b"
    b.mkdir()
    _record(a, A_RUN, started_at=datetime(2026, 9, 21, 9, 0, tzinfo=timezone.utc))
    _record(b, B_RUN, started_at=datetime(2026, 9, 22, 9, 0, tzinfo=timezone.utc))
    return a, b


def _row_counts() -> dict[str, int]:
    """Every table of `am.db` with its row count, over a plain connection."""
    conn = sqlite3.connect(paths.db_path())
    try:
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
            )
        ]
        return {
            table: conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            for table in tables
        }
    finally:
        conn.close()


def test_runs_and_status_of_one_repo_never_show_another_repos_run(two_repos):
    a, _ = two_repos

    listed = runner.invoke(cli.app, ["runs", "--repo-dir", str(a)])
    latest = runner.invoke(cli.app, ["status", "--repo-dir", str(a)])
    dotted = runner.invoke(cli.app, ["runs", "--repo-dir", str(a / ".." / "a")])

    assert paths.db_path().is_file()
    assert listed.exit_code == 0, listed.output
    assert [run["id"] for run in json.loads(listed.stdout)["data"]["runs"]] == [A_RUN]
    assert latest.exit_code == 0, latest.output
    assert json.loads(latest.stdout)["data"]["run"]["id"] == A_RUN
    assert dotted.exit_code == 0, dotted.output
    assert [run["id"] for run in json.loads(dotted.stdout)["data"]["runs"]] == [A_RUN]


@pytest.mark.parametrize(
    "argv",
    [
        ["status", B_RUN],
        ["logs", B_RUN, "card"],
        ["resume", B_RUN],
        ["reset", B_RUN],
        ["cancel", B_RUN],
        ["pause", B_RUN],
    ],
)
def test_a_run_of_another_repo_is_an_unknown_run_and_nothing_is_written(two_repos, argv):
    a, _ = two_repos
    runs_root = paths.data_path() / "runs"
    runs_before = _tree(runs_root)
    rows_before = _row_counts()

    result = runner.invoke(cli.app, [*argv, "--repo-dir", str(a)])

    assert result.exit_code == cli.EXIT_ERROR, result.output
    assert "UnknownRunError" in result.output
    assert f"run {B_RUN!r} is not in the projection" in result.output
    assert _tree(runs_root) == runs_before
    assert _row_counts() == rows_before


def test_runs_on_a_newer_machine_database_is_a_store_schema_error(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    root = tmp_path / "repo"
    root.mkdir()
    path = paths.db_path()
    path.parent.mkdir(parents=True)
    newer = sqlite3.connect(path)
    newer.execute("CREATE TABLE sentinel (x)")
    newer.execute(f"PRAGMA user_version = {store_db.SCHEMA_VERSION + 1}")
    newer.commit()
    newer.close()

    result = _invoke_creating_nothing(tmp_path, ["runs", "--repo-dir", str(root)])

    assert result.exit_code == cli.EXIT_ERROR, result.output
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is False
    assert envelope["error"]["type"] == "StoreSchemaError"
    assert str(path) in envelope["error"]["message"]


def _leave_a_legacy_database() -> None:
    projects = paths.data_path() / "projects"
    projects.mkdir(parents=True, exist_ok=True)
    (projects / f"{'0' * 64}.db").write_bytes(b"")


@pytest.mark.parametrize(
    "argv",
    [
        ["runs"],
        ["status"],
        ["status", RUN_ID],
        ["logs", RUN_ID, "card"],
        ["resume", RUN_ID],
        ["reset", RUN_ID],
        ["cancel", RUN_ID],
        ["pause", RUN_ID],
    ],
)
def test_every_store_command_refuses_an_unmigrated_machine_and_writes_nothing(
    tmp_path, monkeypatch, argv
):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    root = tmp_path / "repo"
    root.mkdir()
    _leave_a_legacy_database()

    result = _invoke_creating_nothing(tmp_path, [*argv, "--repo-dir", str(root)])

    assert result.exit_code == cli.EXIT_ERROR, result.output
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is False
    assert envelope["error"]["type"] == "MigrationRequiredError"
    assert "am migrate" in envelope["error"]["message"]
    assert not paths.db_path().exists()
    assert not (paths.data_path() / "runs").exists()


def test_journal_check_refuses_an_unmigrated_machine_and_writes_nothing(
    tmp_path, monkeypatch
):
    # journal-check is machine-wide and takes no --repo-dir, so it cannot join
    # the parametrization above, which appends one to every argv.
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    _leave_a_legacy_database()

    result = _invoke_creating_nothing(tmp_path, ["journal-check", "--all"])

    assert result.exit_code == cli.EXIT_ERROR, result.output
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is False
    assert envelope["error"]["type"] == "MigrationRequiredError"
    assert "am migrate" in envelope["error"]["message"]
    assert not paths.db_path().exists()
    assert not (paths.data_path() / "runs").exists()
