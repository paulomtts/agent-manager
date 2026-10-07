"""`am backup`: `store.backup.backup` behind brd's envelope.

`am.db` is made with `store_db.open_db` under the per-test data directory and
the command runs in-process through `CliRunner`; nothing spawns a process, so
these are unit tests.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from typer.testing import CliRunner

from agent_manager import cli, paths
from agent_manager.store import db as store_db

runner = CliRunner()


def _backup(*extra: str):
    return runner.invoke(cli.app, ["backup", *extra])


def _envelope(result) -> dict:
    return json.loads(result.stdout)


def _make_db(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    store_db.open_db(repo).close()


def test_backup_prints_path_and_size_envelope(tmp_path):
    _make_db(tmp_path)
    out = tmp_path / "copy.db"

    result = _backup("--out", str(out))

    assert result.exit_code == 0, result.output
    envelope = _envelope(result)
    assert envelope["ok"] is True
    assert set(envelope["data"]) == {"path", "size_bytes"}
    assert envelope["data"]["path"] == str(out)
    assert envelope["data"]["size_bytes"] == os.stat(out).st_size


def test_backup_without_out_writes_the_stamped_default(tmp_path, monkeypatch):
    _make_db(tmp_path)
    monkeypatch.setattr(
        cli, "_utcnow", lambda: datetime(2026, 10, 7, 9, 0, tzinfo=timezone.utc)
    )

    result = _backup()

    assert result.exit_code == 0, result.output
    expected = paths.data_path() / "backups" / "am-20261007T090000Z.db"
    assert _envelope(result)["data"]["path"] == str(expected)
    assert expected.is_file()


def test_backup_over_an_existing_file_is_a_refusal_envelope_at_exit_3(tmp_path):
    _make_db(tmp_path)
    out = tmp_path / "copy.db"
    out.write_bytes(b"precious")

    result = _backup("--out", str(out))

    assert result.exit_code == 3, result.output
    envelope = _envelope(result)
    assert envelope["ok"] is False
    assert envelope["error"]["type"] == "BackupRefusedError"
    assert "target_exists" in envelope["error"]["message"]
    assert str(out) in envelope["error"]["message"]
    assert out.read_bytes() == b"precious"


def test_backup_without_a_database_is_a_refusal_envelope_at_exit_3(tmp_path):
    result = _backup("--out", str(tmp_path / "copy.db"))

    assert result.exit_code == 3, result.output
    envelope = _envelope(result)
    assert envelope["error"]["type"] == "BackupRefusedError"
    assert "no_database" in envelope["error"]["message"]
    assert str(paths.db_path()) in envelope["error"]["message"]
    assert not paths.data_path().exists()


def test_backup_pretty_indents_the_envelope(tmp_path):
    _make_db(tmp_path)
    out = tmp_path / "copy.db"

    result = _backup("--out", str(out), "--pretty")

    assert result.exit_code == 0, result.output
    assert len(result.stdout.strip().splitlines()) > 1
    envelope = _envelope(result)
    assert envelope == {
        "ok": True,
        "data": {"path": str(out), "size_bytes": out.stat().st_size},
    }
