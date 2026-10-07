"""`agent_manager.journal_check`: each run's `events` rows of the node kinds
against its `journal.jsonl`, keyed by `run_seq`.

Rows are inserted with `store_events.insert` into an `am.db` made by
`store_db.open_db` under the per-test data directory; journals are written as
exact JSON lines. Nothing spawns a process, so these are unit tests.
"""

import ast
import inspect
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from legacyhelpers import journal_line, write_journal

from agent_manager import journal_check, paths, runs
from agent_manager.store import db as store_db
from agent_manager.store import events as store_events
from agent_manager.store import journal as store_journal
from agent_manager.store import projects as store_projects

RUN = "20261007T090000Z-aaaaaaaa"
NOW = datetime(2026, 10, 7, 9, 0, tzinfo=timezone.utc)
TS = store_journal.ts_text(datetime(2026, 10, 7, 9, 0, 0, 123456, tzinfo=timezone.utc))
"""A `ts` as the live writer stamps it and the mirror writes it."""
PAYLOAD = {"status": "done"}


class _Rows:
    """An `am.db` under the data directory and one project to insert rows for."""

    def __init__(self, root: Path) -> None:
        root.mkdir(exist_ok=True)
        self.root = root
        self.conn = store_db.open_db(root)
        self.project_id = store_projects.resolve(self.conn, root, now=NOW)
        self.conn.commit()

    def add(
        self,
        seq: int,
        kind: str = "phase_upsert",
        *,
        run_id: str = RUN,
        ts: str = TS,
        payload: dict | None = None,
        source: str = "live",
        project_id: int | None = None,
        **coords,
    ) -> store_events.EventRow:
        row = store_events.insert(
            self.conn,
            project_id=self.project_id if project_id is None else project_id,
            run_id=run_id,
            run_seq=seq,
            ts=ts,
            kind=kind,
            payload=PAYLOAD if payload is None else payload,
            source=source,
            story_id=coords.get("story"),
            card_id=coords.get("card"),
            phase=coords.get("phase"),
            attempt=coords.get("attempt"),
        )
        self.conn.commit()
        return row

    def close(self) -> None:
        self.conn.close()


@pytest.fixture
def rows(tmp_path):
    made = _Rows(tmp_path / "repo")
    yield made
    made.close()


def _line(seq: int, event: str = "phase_upsert", *, run_id: str = RUN, ts: str = TS, payload=None, **coords):
    return journal_line(run_id, seq, ts, event, PAYLOAD if payload is None else payload, **coords)


def _write_text(run_id: str, text: str) -> Path:
    path = paths.data_path() / "runs" / run_id / "journal.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))
    return path


def _only(report: journal_check.JournalCheckReport) -> journal_check.RunCheck:
    assert len(report.runs) == 1
    return report.runs[0]


def _shape(seq: int, *, run_id: str = RUN, ts: str = TS, event: str = "phase_upsert", payload=None, **coords):
    """A line in journal shape: every `JournalLine` key, in its order."""
    return {
        "seq": seq,
        "ts": ts,
        "run_id": run_id,
        "event": event,
        "story": coords.get("story"),
        "card": coords.get("card"),
        "phase": coords.get("phase"),
        "attempt": coords.get("attempt"),
        "payload": PAYLOAD if payload is None else payload,
    }


# ── constructed differences ──────────────────────────────────────────────────


def test_an_agreeing_run_written_as_the_mirror_writes_is_clean(rows):
    made = [rows.add(seq, phase="verify", attempt=1) for seq in (1, 2, 3)]
    write_journal(RUN, [store_events.journal_line(row).model_dump(mode="json") for row in made])

    report = journal_check.check(RUN)

    run = _only(report)
    assert report.clean is True
    assert run.clean is True
    assert run.differences == ()
    assert (run.table_lines, run.file_lines) == (3, 3)
    assert run.journal_present is True
    assert run.journal == str(paths.data_path() / "runs" / RUN / "journal.jsonl")
    assert (run.torn_line, run.unreadable) == (None, None)


def test_a_line_the_mirror_never_wrote_is_missing(rows):
    for seq in (1, 2, 3):
        rows.add(seq, phase="verify", attempt=1)
    write_journal(RUN, [_line(1, phase="verify", attempt=1), _line(3, phase="verify", attempt=1)])

    run = _only(journal_check.check(RUN))

    assert run.differences == (
        journal_check.Difference(
            run_seq=2,
            kind="missing_line",
            field=None,
            table=_shape(2, phase="verify", attempt=1),
            file=None,
        ),
    )
    assert run.clean is False
    assert (run.table_lines, run.file_lines) == (3, 2)


def test_a_line_the_table_does_not_hold_is_extra(rows):
    rows.add(1)
    rows.add(2)
    write_journal(RUN, [_line(1), _line(2), _line(3, story="s1")])

    run = _only(journal_check.check(RUN))

    assert run.differences == (
        journal_check.Difference(
            run_seq=3, kind="extra_line", field=None, table=None, file=_shape(3, story="s1")
        ),
    )


def test_a_differing_payload_is_one_mismatch_with_both_decoded_values(rows):
    rows.add(1, payload={"status": "done"})
    write_journal(RUN, [_line(1, payload={"status": "failed"})])

    run = _only(journal_check.check(RUN))

    assert run.differences == (
        journal_check.Difference(
            run_seq=1,
            kind="mismatch",
            field="payload",
            table={"status": "done"},
            file={"status": "failed"},
        ),
    )


def test_the_same_instant_written_differently_is_a_ts_mismatch(rows):
    rows.add(1, ts="2026-10-07T09:00:00Z")
    write_journal(RUN, [_line(1, ts="2026-10-07T09:00:00+00:00")])

    run = _only(journal_check.check(RUN))

    assert run.differences == (
        journal_check.Difference(
            run_seq=1,
            kind="mismatch",
            field="ts",
            table="2026-10-07T09:00:00Z",
            file="2026-10-07T09:00:00+00:00",
        ),
    )


def test_payload_key_order_does_not_matter(rows):
    rows.add(1, payload={"a": 1, "b": 2})
    record = _line(1, payload={"b": 2, "a": 1})
    _write_text(RUN, json.dumps(record) + "\n")

    assert _only(journal_check.check(RUN)).clean is True


@pytest.mark.parametrize(
    ("table", "file"),
    [({"ok": True}, {"ok": 1}), ({"n": 1}, {"n": 1.0}), ({}, {"k": None})],
)
def test_payload_values_of_another_type_are_a_mismatch(rows, table, file):
    rows.add(1, payload=table)
    write_journal(RUN, [_line(1, payload=file)])

    (difference,) = _only(journal_check.check(RUN)).differences

    assert (difference.field, difference.table, difference.file) == ("payload", table, file)


def test_a_missing_payload_key_reads_as_an_empty_payload(rows):
    rows.add(1, payload={})
    record = _line(1)
    del record["payload"]
    _write_text(RUN, json.dumps(record) + "\n")

    assert _only(journal_check.check(RUN)).clean is True


@pytest.mark.parametrize(
    ("field", "table", "file"),
    [
        ("event", {"kind": "phase_upsert"}, {"event": "attempt_upsert"}),
        ("story", {"story": "s1"}, {"story": "s2"}),
        ("card", {"card": "c1"}, {"card": None}),
        ("phase", {"phase": "verify"}, {"phase": "spec"}),
        ("attempt", {"attempt": 1}, {"attempt": 2}),
    ],
)
def test_each_differing_coordinate_is_one_mismatch_naming_it(rows, field, table, file):
    rows.add(1, **table)
    write_journal(RUN, [_line(1, **file)])

    (difference,) = _only(journal_check.check(RUN)).differences

    column = "kind" if field == "event" else field
    assert difference == journal_check.Difference(
        run_seq=1, kind="mismatch", field=field, table=table[column], file=file[field]
    )


def test_two_differing_fields_are_two_mismatches_in_field_order(rows):
    rows.add(1, attempt=1, phase="verify", payload={"x": 1})
    write_journal(RUN, [_line(1, attempt=2, phase="spec", payload={"x": 2})])

    differences = _only(journal_check.check(RUN)).differences

    assert [d.field for d in differences] == ["phase", "attempt", "payload"]
    assert list(journal_check.FIELDS) == ["event", "ts", "story", "card", "phase", "attempt", "payload"]


def test_attempt_zero_against_a_missing_attempt_is_a_mismatch(rows):
    # Review Focus: 0 is not None.
    rows.add(1, attempt=0)
    record = _line(1)
    del record["attempt"]
    _write_text(RUN, json.dumps(record) + "\n")

    (difference,) = _only(journal_check.check(RUN)).differences

    assert (difference.field, difference.table, difference.file) == ("attempt", 0, None)


def test_rows_of_kinds_no_journal_holds_are_ignored(rows):
    rows.add(1)
    rows.add(2, "lease_acquired")
    rows.add(3)
    write_journal(RUN, [_line(1), _line(3)])

    run = _only(journal_check.check(RUN))

    assert run.clean is True
    assert (run.table_lines, run.file_lines) == (2, 2)


def test_a_file_line_of_a_kind_no_journal_holds_or_an_unknown_kind_is_extra(rows):
    rows.add(1)
    rows.add(4, "lease_acquired")
    write_journal(
        RUN,
        [_line(1), _line(2, "control_requested"), _line(3, "future_kind"), _line(4, "lease_acquired")],
    )

    run = _only(journal_check.check(RUN))

    assert [(d.run_seq, d.kind, d.file["event"]) for d in run.differences] == [
        (2, "extra_line", "control_requested"),
        (3, "extra_line", "future_kind"),
        (4, "extra_line", "lease_acquired"),
    ]
    assert (run.table_lines, run.file_lines) == (1, 4)


def test_no_journal_file_makes_every_row_missing(rows):
    rows.add(1)
    rows.add(2)

    run = _only(journal_check.check(RUN))

    assert run.journal_present is False
    assert [(d.run_seq, d.kind) for d in run.differences] == [(1, "missing_line"), (2, "missing_line")]
    assert run.file_lines == 0
    assert run.clean is False


def test_an_empty_journal_file_is_present_and_every_row_is_missing(rows):
    # Review Focus: a 0-byte journal is readable and holds no line.
    rows.add(1)
    rows.add(2)
    _write_text(RUN, "")

    run = _only(journal_check.check(RUN))

    assert run.journal_present is True
    assert run.unreadable is None
    assert run.file_lines == 0
    assert [(d.run_seq, d.kind) for d in run.differences] == [(1, "missing_line"), (2, "missing_line")]


def test_blank_lines_in_the_journal_are_not_lines(rows):
    # Review Focus: read_verbatim skips blank lines; they are not counted.
    rows.add(1)
    rows.add(2)
    _write_text(RUN, "\n" + json.dumps(_line(1)) + "\n\n   \n" + json.dumps(_line(2)) + "\n")

    run = _only(journal_check.check(RUN))

    assert run.clean is True
    assert run.file_lines == 2


def test_a_torn_tail_the_import_skipped_is_reported_and_clean(rows):
    rows.add(1, source="imported")
    rows.add(2, source="imported")
    write_journal(RUN, [_line(1), _line(2)], tail='{"seq": 3, "ts"')

    run = _only(journal_check.check(RUN))

    assert run.torn_line == 3
    assert run.differences == ()
    assert run.clean is True
    assert run.file_lines == 2


def test_a_torn_tail_of_a_row_the_table_holds_is_its_missing_line(rows):
    for seq in (1, 2, 3):
        rows.add(seq)
    write_journal(RUN, [_line(1), _line(2)], tail='{"seq": 3, "ts"')

    run = _only(journal_check.check(RUN))

    assert run.torn_line == 3
    assert [(d.run_seq, d.kind) for d in run.differences] == [(3, "missing_line")]
    assert run.clean is False


@pytest.mark.parametrize(
    ("second", "reason"),
    [
        ("not json", "not JSON"),
        (json.dumps(_line(2, run_id="20261007T090000Z-bbbbbbbb")), "run_id is"),
    ],
)
def test_an_unreadable_journal_is_reported_unclean_without_line_differences(rows, second, reason):
    rows.add(1)
    rows.add(2)
    path = _write_text(RUN, json.dumps(_line(1)) + "\n" + second + "\n" + json.dumps(_line(3)) + "\n")

    run = _only(journal_check.check(RUN))

    assert run.unreadable is not None
    assert run.unreadable.line == 2
    assert reason in run.unreadable.why
    assert str(path) not in run.unreadable.why
    assert run.differences == ()
    assert run.journal_present is True
    assert (run.table_lines, run.file_lines) == (2, 0)
    assert run.clean is False


def test_an_imported_run_agrees_with_its_own_journal(rows):
    lines = [
        _line(1, "run_upsert", ts="2026-10-07T09:00:00+00:00", payload={"z": 1, "a": [1, 2]}),
        _line(2, "story_upsert", ts="2026-10-07T09:00:01.5+00:00", story="s1"),
        _line(3, "phase_upsert", ts="2026-10-07T09:00:02Z", story="s1", card="c1", phase="spec", attempt=1),
    ]
    _write_text(RUN, "".join(json.dumps(line) + "\n" for line in lines))
    for line in lines:
        rows.add(
            line["seq"], line["event"], ts=line["ts"], payload=line["payload"], source="imported",
            story=line["story"], card=line["card"], phase=line["phase"], attempt=line["attempt"],
        )

    assert _only(journal_check.check(RUN)).clean is True


def test_a_run_of_another_project_is_checked(rows, tmp_path):
    # Review Focus: the checker is machine-wide.
    other = store_projects.resolve(rows.conn, _mkdir(tmp_path / "other"), now=NOW)
    rows.conn.commit()
    rows.add(1, project_id=other)

    assert _only(journal_check.check(RUN)).differences[0].kind == "missing_line"
    assert [run.run_id for run in journal_check.check(None).runs] == [RUN]


def _mkdir(path: Path) -> Path:
    path.mkdir()
    return path


def test_a_row_whose_payload_is_not_json_propagates(rows):
    # Review Focus: never swallowed; a ValueError the CLI renders at exit 3.
    rows.conn.execute(
        "INSERT INTO events (project_id, run_id, run_seq, ts, kind, payload, source)"
        " VALUES (?, ?, 1, ?, 'phase_upsert', 'not json', 'live')",
        (rows.project_id, RUN, TS),
    )
    rows.conn.commit()

    with pytest.raises(json.JSONDecodeError):
        journal_check.check(RUN)


def test_compare_without_a_journal_is_pure(rows):
    row = rows.add(1)
    path = Path("/nowhere/journal.jsonl")

    run = journal_check.compare(RUN, [row], None, path)

    assert run.journal == str(path)
    assert run.journal_present is False
    assert [d.kind for d in run.differences] == ["missing_line"]
    assert not path.parent.exists()


# ── --all ────────────────────────────────────────────────────────────────────


def test_all_covers_runs_with_rows_or_a_journal_in_run_id_order(rows):
    rows.add(1, run_id="run-a")
    write_journal("run-a", [_line(1, run_id="run-a")])
    rows.add(1, run_id="run-b")
    write_journal("run-c", [_line(1, run_id="run-c")])
    logs_only = paths.data_path() / "runs" / "run-d"
    logs_only.mkdir(parents=True)
    (logs_only / "stdout.log").write_text("x")

    report = journal_check.check(None)

    assert [run.run_id for run in report.runs] == ["run-a", "run-b", "run-c"]
    assert [run.clean for run in report.runs] == [True, False, False]
    assert report.clean is False


def test_all_keeps_going_past_an_unreadable_run(rows):
    for run_id in ("run-a", "run-b", "run-c", "run-d"):
        rows.add(1, run_id=run_id)
    write_journal("run-a", [_line(1, run_id="run-a")])
    _write_text("run-b", "not json\n")
    rows.add(2, run_id="run-c")
    write_journal("run-c", [_line(1, run_id="run-c")])
    write_journal("run-d", [_line(1, run_id="run-d")])

    report = journal_check.check(None)

    assert [(run.run_id, run.clean) for run in report.runs] == [
        ("run-a", True), ("run-b", False), ("run-c", False), ("run-d", True),
    ]
    assert report.runs[1].unreadable is not None
    assert report.clean is False


def test_all_clean_runs_make_a_clean_report(rows):
    for run_id in ("run-a", "run-b"):
        rows.add(1, run_id=run_id)
        write_journal(run_id, [_line(1, run_id=run_id)])

    report = journal_check.check(None)

    assert report.clean is True
    assert len(report.runs) == 2


def test_all_on_nothing_is_clean_and_empty():
    assert journal_check.check(None) == journal_check.JournalCheckReport(clean=True, runs=())


# ── refusals ─────────────────────────────────────────────────────────────────


def test_an_unknown_run_is_refused():
    with pytest.raises(runs.UnknownRunError, match=RUN):
        journal_check.check(RUN)


def test_a_run_directory_without_a_journal_or_rows_is_refused(rows):
    rows.add(1, run_id="run-other")
    logs_only = paths.data_path() / "runs" / RUN
    logs_only.mkdir(parents=True)
    (logs_only / "stdout.log").write_text("x")

    with pytest.raises(runs.UnknownRunError, match=RUN):
        journal_check.check(RUN)


# ── read-only ────────────────────────────────────────────────────────────────


def _snapshot(root: Path) -> dict[str, bytes | None]:
    """Every path under `root`, with a file's bytes and `None` for a directory."""
    return {
        str(path.relative_to(root)): (path.read_bytes() if path.is_file() else None)
        for path in root.rglob("*")
    }


def test_check_changes_nothing_under_an_existing_store(rows):
    rows.add(1)
    rows.add(2)
    write_journal(RUN, [_line(1)], tail='{"torn')
    rows.add(1, run_id="run-b")
    rows.close()
    root = paths.data_path()
    assert not (root / "am.db-wal").exists()
    before = _snapshot(root)

    journal_check.check(None)
    journal_check.check(RUN)
    journal_check.check("run-b")

    assert _snapshot(root) == before


def test_check_creates_nothing_without_a_store():
    journal_check.check(None)
    with pytest.raises(runs.UnknownRunError):
        journal_check.check(RUN)

    assert not paths.data_path().exists()


def test_journal_check_issues_no_sql():
    tree = ast.parse(inspect.getsource(journal_check))
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert "sqlite3" not in imported
