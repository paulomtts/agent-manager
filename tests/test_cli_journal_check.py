"""`am journal-check`: `journal_check.check` behind brd's envelope.

Rows go into an `am.db` made by `store_db.open_db` under the per-test data
directory, journals are written as JSON lines, and the command runs in-process
through `CliRunner`; nothing spawns a process, so these are unit tests.
"""

import json
from datetime import datetime, timezone

import pytest
from legacyhelpers import journal_line, write_journal
from typer.testing import CliRunner

from agent_manager import cli, paths
from agent_manager.store import db as store_db
from agent_manager.store import events as store_events
from agent_manager.store import projects as store_projects

runner = CliRunner()

RUN = "20261007T090000Z-aaaaaaaa"
OTHER = "20261007T090000Z-bbbbbbbb"
TS = "2026-10-07T09:00:00+00:00"
RUN_KEYS = {
    "run_id", "journal", "journal_present", "table_lines", "file_lines",
    "torn_line", "unreadable", "clean", "differences",
}


def _check(*argv: str):
    return runner.invoke(cli.app, ["journal-check", *argv])


def _rows(tmp_path, *run_ids: str) -> None:
    """One `phase_upsert` row at `run_seq` 1 for each run, committed."""
    repo = tmp_path / "repo"
    repo.mkdir()
    conn = store_db.open_db(repo)
    try:
        project_id = store_projects.resolve(
            conn, repo, now=datetime(2026, 10, 7, 9, 0, tzinfo=timezone.utc)
        )
        for run_id in run_ids:
            store_events.insert(
                conn, project_id=project_id, run_id=run_id, run_seq=1, ts=TS,
                kind="phase_upsert", payload={}, source="live",
            )
        conn.commit()
    finally:
        conn.close()


def test_a_dirty_run_is_an_ok_envelope_at_exit_0(tmp_path):
    _rows(tmp_path, RUN)

    result = _check(RUN)

    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is True
    assert set(envelope["data"]) == {"clean", "runs"}
    assert envelope["data"]["clean"] is False
    (run,) = envelope["data"]["runs"]
    assert set(run) == RUN_KEYS
    assert run["clean"] is False
    (difference,) = run["differences"]
    assert set(difference) == {"run_seq", "kind", "field", "table", "file"}
    assert difference["kind"] == "missing_line"
    assert difference["table"]["seq"] == 1


def test_all_lists_every_run(tmp_path):
    _rows(tmp_path, RUN, OTHER)
    write_journal(RUN, [journal_line(RUN, 1, TS, "phase_upsert")])

    result = _check("--all")

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)["data"]
    assert [run["run_id"] for run in data["runs"]] == [RUN, OTHER]
    assert [run["clean"] for run in data["runs"]] == [True, False]
    assert data["clean"] is False


@pytest.mark.parametrize("argv", [[], [RUN, "--all"]])
def test_neither_or_both_of_run_and_all_is_a_cli_error_creating_nothing(argv):
    result = _check(*argv)

    assert result.exit_code == cli.EXIT_ERROR, result.output
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is False
    assert envelope["error"]["type"] == "CliError"
    assert "--all" in envelope["error"]["message"]
    assert not paths.data_path().exists()


def test_an_unknown_run_is_an_unknown_run_error_at_exit_3():
    result = _check(RUN)

    assert result.exit_code == cli.EXIT_ERROR, result.output
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is False
    assert envelope["error"]["type"] == "UnknownRunError"
    assert RUN in envelope["error"]["message"]


def test_pretty_indents_the_same_envelope(tmp_path):
    _rows(tmp_path, RUN)

    plain = _check(RUN)
    pretty = _check(RUN, "--pretty")

    assert pretty.exit_code == 0, pretty.output
    assert "\n  " in pretty.stdout
    assert json.loads(pretty.stdout) == json.loads(plain.stdout)
