"""`agent_manager.export`: one run's `events` rows as journal-shaped lines, and
the `am migrate` -> export round trip. Legacy data is built with
`tests/legacyhelpers.py` under the per-test data directory and `migrate.migrate`
runs in-process; nothing spawns a process, so these are unit tests.
"""

import ast
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from legacyhelpers import (
    NOW,
    full_rows,
    journal_line,
    project_file,
    run_row,
    tree,
    write_db,
    write_journal,
)

from agent_manager import export, migrate, models, paths, runs
from agent_manager.store import db as store_db
from agent_manager.store import events as store_events
from agent_manager.store import journal as store_journal
from agent_manager.store import queries as store_queries

STAMP_Z = store_journal.ts_text(
    datetime(2026, 10, 7, 10, 0, 0, 123456, tzinfo=timezone.utc)
)
"""A `ts` as the live writer stamps it: `Z` with microseconds."""

KEYS = ["attempt", "card", "event", "gseq", "payload", "phase", "run_id", "seq", "story", "ts"]


def _ts(second: int) -> str:
    """A `ts` in the older `+00:00` form."""
    return f"2026-10-07T10:00:{second:02d}+00:00"


def _write_legacy(alpha: Path, beta: Path) -> dict[str, Path]:
    """Two legacy projects and three runs' journals; returns each journal path.

    run-a: all five node kinds, retired attempt keys, a float `1.0`, nested
    non-ASCII keys, `ts` in both forms. run-b: a torn tail. run-c: a run whose
    status is the legacy cancel spelling, in its row and its payload.
    """
    rows_b = full_rows("run-b", beta)
    rows_b["runs"].append(run_row("run-c", beta, status=models.LEGACY_CANCELED))
    write_db(project_file(alpha), full_rows("run-a", alpha))
    write_db(project_file(beta), rows_b)
    return {
        "run-a": write_journal(
            "run-a",
            [
                journal_line("run-a", 1, STAMP_Z, "run_upsert",
                             {"status": "started", "workflow": "task"}),
                journal_line("run-a", 2, _ts(1), "story_upsert",
                             {"status": "running", "title": "t"}, story="s1"),
                journal_line("run-a", 3, _ts(2), "subtask_upsert", {"status": "running"},
                             story="s1", card="c1"),
                journal_line("run-a", 4, _ts(3), "phase_upsert",
                             {"kind": "agent", "status": "done"},
                             story="s1", card="c1", phase="spec"),
                journal_line("run-a", 5, _ts(4), "attempt_upsert",
                             {"status": "done", "tokens_in": 10, "tokens_out": 20,
                              "cost": 0.5, "ratio": 1.0,
                              "nested": {"é": [1.0, 2], "b": {"z": 1, "a": "ü"}}},
                             story="s1", card="c1", phase="spec", attempt=1),
            ],
        ),
        "run-b": write_journal(
            "run-b",
            [
                journal_line("run-b", 1, _ts(1), "run_upsert", {"status": "started"}),
                journal_line("run-b", 2, _ts(5), "story_upsert", {"status": "running"},
                             story="s1"),
            ],
            tail='{"seq": 3, "ts',
        ),
        "run-c": write_journal(
            "run-c",
            [
                journal_line("run-c", 1, _ts(2), "run_upsert", {"status": "started"}),
                journal_line("run-c", 2, _ts(6), "run_upsert",
                             {"status": models.LEGACY_CANCELED}),
            ],
        ),
    }


def _migrate() -> None:
    migrate.migrate(now=NOW, host="here", alive=lambda pid: False)


@pytest.fixture
def journals(tmp_path) -> dict[str, Path]:
    """The legacy data dir, not yet migrated."""
    alpha, beta = tmp_path / "alpha", tmp_path / "beta"
    alpha.mkdir()
    beta.mkdir()
    return _write_legacy(alpha, beta)


@pytest.fixture
def migrated(journals) -> dict[str, Path]:
    """The legacy data dir after `am migrate`."""
    _migrate()
    return journals


def _complete_lines(path: Path) -> list[str]:
    """The file's lines, split on `\\n`, a torn final fragment dropped."""
    complete, _, _ = path.read_text(encoding="utf-8").rpartition("\n")
    return complete.split("\n") if complete else []


def _without_gseq(lines: list[str]) -> list[str]:
    return [
        json.dumps(
            {key: value for key, value in json.loads(text).items() if key != "gseq"},
            sort_keys=True,
        )
        for text in lines
    ]


def _gseqs(run_id: str) -> dict[int, int]:
    """`run_seq` -> `events.seq` of `run_id`, read straight from the table."""
    conn = store_db.open_db_for_reading(Path("."))
    try:
        rows = conn.execute(
            "SELECT run_seq, seq FROM events WHERE run_id = ?", (run_id,)
        ).fetchall()
    finally:
        conn.close()
    return {row[0]: row[1] for row in rows}


def _row(**overrides) -> store_events.EventRow:
    fields = {
        "seq": 7,
        "project_id": 1,
        "run_id": "run-x",
        "run_seq": 2,
        "ts": "2026-10-07T10:00:01+00:00",
        "kind": "run_upsert",
        "story_id": None,
        "card_id": None,
        "phase": None,
        "attempt": None,
        "schema": 1,
        "payload": {"b": 1.0, "a": "é"},
        "source": "imported",
    }
    fields.update(overrides)
    return store_events.EventRow(**fields)


def test_export_imports_no_sqlite3():
    tree_ = ast.parse(Path(export.__file__).read_text())
    imported = {
        alias.name for node in ast.walk(tree_) if isinstance(node, ast.Import)
        for alias in node.names
    } | {node.module for node in ast.walk(tree_) if isinstance(node, ast.ImportFrom)}
    assert "sqlite3" not in imported


def test_a_line_keeps_ts_text_and_has_every_key():
    text = export.line(_row())

    assert text == (
        r'{"attempt": null, "card": null, "event": "run_upsert", "gseq": 7,'
        r' "payload": {"a": "\u00e9", "b": 1.0}, "phase": null, "run_id": "run-x",'
        r' "seq": 2, "story": null, "ts": "2026-10-07T10:00:01+00:00"}'
    )
    assert list(json.loads(text)) == KEYS
    assert not {"schema", "source", "project_id"} & set(json.loads(text))


def test_a_line_escapes_line_breaks_in_payload_strings():
    message = "a\nb\u2028c\r"

    text = export.line(_row(payload={"message": message}))

    assert "\n" not in text and "\r" not in text and "\u2028" not in text
    assert json.loads(text)["payload"]["message"] == message


def test_round_trip_over_a_migrated_data_dir_reproduces_every_journal_line(journals):
    before = {run_id: path.read_bytes() for run_id, path in journals.items()}
    _migrate()
    after_migrate = tree(paths.data_path())

    exported = {run_id: export.export_run(run_id) for run_id in journals}

    assert tree(paths.data_path()) == after_migrate
    for run_id, path in journals.items():
        assert path.read_bytes() == before[run_id]
        assert _without_gseq(exported[run_id]) == _complete_lines(path)
        decoded = [json.loads(text) for text in exported[run_id]]
        gseqs = _gseqs(run_id)
        assert [line["gseq"] for line in decoded] == [gseqs[line["seq"]] for line in decoded]
    assert len(exported["run-b"]) == 2
    timestamps = [json.loads(text)["ts"] for text in exported["run-a"]]
    assert timestamps[0] == STAMP_Z
    assert timestamps[1] == _ts(1)


def test_a_migrated_cancelled_run_lists_as_canceled_and_exports_unchanged(migrated):
    conn = store_db.open_db_for_reading(Path("."))
    try:
        summaries = {
            summary.id: summary
            for summary in store_queries.list_runs(
                conn, project_id=store_queries.ALL_PROJECTS
            )
        }
    finally:
        conn.close()

    lines = export.export_run("run-c")

    assert summaries["run-c"].status == models.CANCELED
    assert _without_gseq(lines) == _complete_lines(migrated["run-c"])
    assert json.loads(lines[-1])["payload"]["status"] == models.LEGACY_CANCELED


def test_new_kinds_and_unknown_kinds_are_exported_in_run_seq_order(migrated):
    conn = store_db.open_db(Path("."))
    try:
        project_id = conn.execute(
            "SELECT project_id FROM events WHERE run_id = 'run-c' LIMIT 1"
        ).fetchone()[0]
        common = {"project_id": project_id, "run_id": "run-c", "ts": STAMP_Z, "source": "live"}
        with store_db.immediate(conn):
            store_events.insert(
                conn, **common, kind="lease_acquired", payload={"token": "t1"}, run_seq=5
            )
            store_events.insert(
                conn, **common, kind="control_requested", payload={"command": "pause"},
                story_id="s1",
            )
            store_events.insert(
                conn, **common, kind="future_kind", payload={"x": [1.0]}, run_seq=3
            )
    finally:
        conn.close()

    lines = [json.loads(text) for text in export.export_run("run-c")]

    assert [line["seq"] for line in lines] == [1, 2, 3, 5, 6]
    assert [line["event"] for line in lines] == [
        "run_upsert", "run_upsert", "future_kind", "lease_acquired", "control_requested",
    ]
    assert all(list(line) == KEYS for line in lines)
    future = lines[2]
    assert (future["story"], future["card"], future["phase"], future["attempt"]) == (
        None, None, None, None,
    )
    assert future["payload"] == {"x": [1.0]}
    assert lines[4]["story"] == "s1"
    # future_kind was inserted after lease_acquired: a larger gseq, a smaller seq.
    assert future["gseq"] > lines[3]["gseq"]


def test_an_unknown_run_raises_and_creates_nothing_beside_an_am_db(migrated):
    before = tree(paths.data_path())

    with pytest.raises(runs.UnknownRunError, match="'nope'"):
        export.export_run("nope")

    assert tree(paths.data_path()) == before


def test_an_unknown_run_raises_and_creates_nothing_without_an_am_db():
    with pytest.raises(runs.UnknownRunError, match="'nope'"):
        export.export_run("nope")

    assert not paths.data_path().exists()


def test_a_run_with_a_runs_row_and_no_events_exports_nothing(tmp_path):
    alpha = tmp_path / "alpha"
    alpha.mkdir()
    write_db(project_file(alpha), full_rows("run-a", alpha))
    _migrate()

    assert export.export_run("run-a") == []


# -- write_export: --out ------------------------------------------------------


def _refusal(run_id: str, out: Path) -> export.ExportRefusedError:
    with pytest.raises(export.ExportRefusedError) as caught:
        export.write_export(run_id, out)
    return caught.value


def test_write_export_writes_the_lines_and_reports_the_count(migrated, tmp_path):
    out = tmp_path / "out.jsonl"

    result = export.write_export("run-a", out)

    lines = export.export_run("run-a")
    assert result == export.ExportResult(run_id="run-a", path=out, lines=5)
    assert len(lines) == 5
    assert out.read_text(encoding="utf-8") == "".join(f"{text}\n" for text in lines)


@pytest.mark.parametrize("kind", ["file", "directory", "dangling_symlink"])
def test_write_export_refuses_an_existing_target_and_leaves_it_unchanged(
    migrated, tmp_path, kind
):
    out = tmp_path / "out.jsonl"
    if kind == "file":
        out.write_bytes(b"precious")
    elif kind == "directory":
        out.mkdir()
    else:
        out.symlink_to(tmp_path / "nowhere")

    error = _refusal("run-a", out)

    assert error.reason == "target_exists"
    assert error.path == out
    assert "(target_exists)" in str(error) and str(out) in str(error)
    if kind == "file":
        assert out.read_bytes() == b"precious"
    elif kind == "directory":
        assert list(out.iterdir()) == []
    else:
        assert out.is_symlink() and not (tmp_path / "nowhere").exists()


def test_write_export_refuses_a_missing_parent_directory(migrated, tmp_path):
    out = tmp_path / "missing" / "out.jsonl"

    error = _refusal("run-a", out)

    assert error.reason == "no_target_dir"
    assert error.path == out
    assert not (tmp_path / "missing").exists()


def test_write_export_refuses_any_runs_journal_path(migrated):
    journal = paths.run_dir("run-a") / store_journal.JOURNAL_NAME
    other = paths.run_dir("other") / store_journal.JOURNAL_NAME
    before = journal.read_bytes()

    # The existing journal is refused as journal_path, not target_exists.
    assert _refusal("run-a", journal).reason == "journal_path"
    assert _refusal("run-a", other).reason == "journal_path"
    assert journal.read_bytes() == before
    assert not other.exists()


def test_write_export_refuses_a_symlink_to_a_journal(migrated, tmp_path):
    journal = migrated["run-b"]
    before = journal.read_bytes()
    link = tmp_path / "link.jsonl"
    link.symlink_to(journal)

    assert _refusal("run-a", link).reason == "journal_path"
    assert journal.read_bytes() == before


def test_write_export_writes_a_journal_named_file_outside_the_runs_dir(migrated, tmp_path):
    out = tmp_path / "elsewhere" / "run-a" / store_journal.JOURNAL_NAME
    out.parent.mkdir(parents=True)

    result = export.write_export("run-a", out)

    assert result.lines == 5
    assert out.is_file()


def test_write_export_makes_a_relative_or_home_target_absolute(
    migrated, tmp_path, monkeypatch
):
    work = tmp_path / "work"
    home = tmp_path / "home"
    work.mkdir()
    home.mkdir()
    monkeypatch.chdir(work)
    monkeypatch.setenv("HOME", str(home))

    relative = export.write_export("run-a", Path("rel.jsonl"))
    homed = export.write_export("run-a", Path("~/out.jsonl"))

    assert relative.path == Path.cwd() / "rel.jsonl"
    assert relative.path.is_absolute() and relative.path.is_file()
    assert homed.path == home / "out.jsonl"
    assert homed.path.is_file()


def test_an_unknown_run_with_out_creates_no_file(migrated, tmp_path):
    out = tmp_path / "out.jsonl"

    with pytest.raises(runs.UnknownRunError):
        export.write_export("nope", out)

    assert not out.exists()
