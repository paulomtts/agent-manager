"""`am export`: `agent_manager.export` behind the CLI. Without `--out` stdout is
only the lines; with it, brd's envelope. `am.db` is made with `store_db.open_db`
under the per-test data directory and the command runs in-process through
`CliRunner`; nothing spawns a process, so these are unit tests.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agent_manager import cli, export, paths
from agent_manager.store import db as store_db
from agent_manager.store import events as store_events
from agent_manager.store import projects as store_projects

runner = CliRunner()

RUN = "20261007T100000Z-0a0b0c0d"
TS = "2026-10-07T10:00:00+00:00"
NOW = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
UNKNOWN = (
    "run 'nope' is not in the projection"
    " (`agent-manager runs --all-projects` lists the ones that are)"
)


def _seed(tmp_path: Path, *, events: bool = True) -> None:
    """An `am.db` holding a `runs` row for RUN and, with `events`, two events."""
    repo = tmp_path / "repo"
    repo.mkdir()
    conn = store_db.open_db(repo)
    try:
        with store_db.immediate(conn):
            project_id = store_projects.resolve(conn, repo, now=NOW)
            conn.execute(
                "INSERT INTO runs (project_id, id, workflow, repo_dir, base_branch,"
                " branch_prefix, status, started_at, config)"
                " VALUES (?, ?, 'task', ?, 'main', 'am/', 'started', ?, '{}')",
                (project_id, RUN, str(repo), TS),
            )
            if events:
                common = {"project_id": project_id, "run_id": RUN, "ts": TS, "source": "live"}
                store_events.insert(
                    conn, **common, kind="run_upsert", payload={"status": "started"}
                )
                store_events.insert(
                    conn, **common, kind="story_upsert",
                    payload={"status": "running", "title": "café"}, story_id="s1",
                )
    finally:
        conn.close()


def _export(*argv: str):
    return runner.invoke(cli.app, ["export", *argv])


def test_export_prints_only_the_lines(tmp_path):
    _seed(tmp_path)

    result = _export(RUN)

    assert result.exit_code == 0, result.output
    lines = export.export_run(RUN)
    assert len(lines) == 2
    assert result.stdout == "\n".join(lines) + "\n"


def test_export_pretty_never_changes_the_lines(tmp_path):
    _seed(tmp_path)

    plain = _export(RUN)
    pretty = _export(RUN, "--pretty")

    assert pretty.exit_code == 0, pretty.output
    assert pretty.stdout == plain.stdout


def test_export_of_an_unknown_run_is_an_envelope_and_exit_3(tmp_path):
    _seed(tmp_path)

    result = _export("nope")

    assert result.exit_code == cli.EXIT_ERROR, result.output
    assert json.loads(result.stdout) == {
        "ok": False,
        "error": {"type": "UnknownRunError", "message": UNKNOWN},
    }


def test_export_out_prints_the_success_envelope(tmp_path):
    _seed(tmp_path)
    out = tmp_path / "out.jsonl"

    result = _export(RUN, "--out", str(out))

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {
        "ok": True,
        "data": {"run_id": RUN, "path": str(out), "lines": 2},
    }
    assert out.read_text(encoding="utf-8") == _export(RUN).stdout


@pytest.mark.parametrize("reason", ["journal_path", "target_exists", "no_target_dir"])
def test_export_out_refusals_are_envelopes(tmp_path, reason):
    _seed(tmp_path)
    out = {
        "journal_path": paths.data_path() / "runs" / RUN / "journal.jsonl",
        "target_exists": tmp_path / "taken.jsonl",
        "no_target_dir": tmp_path / "missing" / "out.jsonl",
    }[reason]
    if reason == "target_exists":
        out.write_bytes(b"precious")

    result = _export(RUN, "--out", str(out), "--pretty")

    assert result.exit_code == cli.EXIT_ERROR, result.output
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is False
    assert envelope["error"]["type"] == "ExportRefusedError"
    assert f"({reason})" in envelope["error"]["message"]
    assert result.stdout.startswith("{\n  ")
    if reason == "target_exists":
        assert out.read_bytes() == b"precious"
    else:
        assert not out.exists()


def test_export_refused_error_rides_the_handled_tuple():
    assert export.ExportRefusedError in cli.HANDLED


def test_export_of_a_run_with_no_events_prints_nothing(tmp_path):
    _seed(tmp_path, events=False)

    result = _export(RUN)

    assert result.exit_code == 0, result.output
    assert result.stdout == ""
