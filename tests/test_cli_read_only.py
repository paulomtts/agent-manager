"""The read-only commands create nothing under the data directory.

`am runs`, `am status`, `am logs`, `am watch` and `am run --dry-run` against a
repository with no projection leave the data directory byte-for-byte as they
found it, and still answer as they do for an unknown run.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agent_manager import cli, models, paths
from agent_manager import store as store_module

runner = CliRunner()

RUN_ID = "20260921T090000Z-cbe34d00"


def _tree(root: Path) -> set[str]:
    """Every path under `root`, relative, directories included."""
    if not root.exists():
        return set()
    return {str(path.relative_to(root)) for path in root.rglob("*")}


def _record(root: Path, run_id: str = RUN_ID) -> None:
    opened = store_module.Store.open(root, run_id)
    try:
        opened.record_run(
            models.Run(
                id=run_id,
                workflow="task",
                repo_dir=root,
                base_branch="main",
                branch_prefix="m1",
                status="started",
                started_at=datetime(2026, 9, 21, 9, 0, tzinfo=timezone.utc),
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
    assert json.loads(result.stdout) == {"ok": True, "data": {"runs": []}}


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
    held = store_module.open_db(root)
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

    assert paths.project_db_location(root).is_file()
