"""Behaviour of `agent_manager.store.journal`: a run's JSONL journal, its line
envelope and event kinds, reading a journal file and reading it verbatim.

Real JSONL files under `tmp_path`; nothing spawns a process, so these are unit
tests. The `repo` fixture redirects `XDG_DATA_HOME` and `HOME`.
"""

import ast
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import get_args

import pytest
from legacyhelpers import journal_line, write_journal
from pydantic import ValidationError

from agent_manager import models, paths, store
from agent_manager.store import journal as store_journal

RUN_ID = "run-2026-09-23-01"

_REPO = Path(__file__).resolve().parents[2]

_JOURNAL_NAMES = (
    "Journal",
    "JournalLine",
    "EventKind",
    "JournalError",
    "MissingJournalError",
    "CorruptJournalError",
    "JOURNAL_NAME",
    "_EVENT_KINDS",
    "NODE_KINDS",
    "_UnknownEventLine",
    "ts_text",
    "_TS",
    "VerbatimLine",
    "VerbatimJournal",
    "UnimportableLineError",
    "read_verbatim",
)


def test_journal_is_a_leaf_module_of_the_store_package():
    for cls in (
        store_journal.Journal,
        store_journal.JournalLine,
        store_journal.JournalError,
        store_journal.MissingJournalError,
        store_journal.CorruptJournalError,
    ):
        assert cls.__module__ == "agent_manager.store.journal"
    assert issubclass(store_journal.JournalError, RuntimeError)
    assert issubclass(store_journal.MissingJournalError, store_journal.JournalError)
    assert issubclass(store_journal.CorruptJournalError, store_journal.JournalError)
    assert set(get_args(store_journal.EventKind)) == {
        "run_upsert",
        "story_upsert",
        "subtask_upsert",
        "phase_upsert",
        "attempt_upsert",
        "control_requested",
        "control_handled",
        "lease_acquired",
        "lease_taken_over",
        "claim_conflict",
    }
    assert store_journal.JOURNAL_NAME == "journal.jsonl"


def test_node_kinds_are_the_five_tree_upserts():
    assert store_journal.NODE_KINDS == frozenset(
        {"run_upsert", "story_upsert", "subtask_upsert", "phase_upsert", "attempt_upsert"}
    )
    assert store_journal.NODE_KINDS <= set(get_args(store_journal.EventKind))


_LEASE_AND_CONTROL_KINDS = (
    "control_requested",
    "control_handled",
    "lease_acquired",
    "lease_taken_over",
    "claim_conflict",
)


@pytest.mark.parametrize("kind", _LEASE_AND_CONTROL_KINDS)
def test_a_journal_line_of_a_lease_or_control_kind_validates_and_is_no_node_kind(kind):
    line = store_journal.JournalLine(
        seq=1,
        ts=datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc),
        run_id=RUN_ID,
        event=kind,
        payload={"token": "t1"},
    )

    assert line.event == kind
    assert kind not in store_journal.NODE_KINDS


def test_the_store_package_does_not_re_export_journal_names():
    assert [name for name in _JOURNAL_NAMES if hasattr(store, name)] == []
    # Were `os` bound on the package, a test patching `store.os` would patch
    # the shared module and pass while naming the wrong one.
    assert not hasattr(store, "os")


def test_journal_imports_only_the_stdlib_pydantic_and_paths():
    # The AST, not `sys.modules`: importing `agent_manager.store.journal` always
    # runs the package `__init__` first, so `sys.modules` cannot tell them apart.
    tree = ast.parse(Path(store_journal.__file__).read_text())
    outside: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] not in sys.stdlib_module_names:
                    outside.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                outside.append("." * node.level + module)
            elif module == "agent_manager":
                outside.extend(
                    f"agent_manager.{alias.name}"
                    for alias in node.names
                    if alias.name != "paths"
                )
            elif module.split(".")[0] not in (*sys.stdlib_module_names, "pydantic"):
                outside.append(module)
    assert outside == []


_THROUGH_THE_PACKAGE = re.compile(
    r"store(_module)?\.(Journal|JournalLine|EventKind|JournalError|MissingJournalError"
    r"|CorruptJournalError|JOURNAL_NAME|_EVENT_KINDS|_UnknownEventLine)\b"
    r"|from agent_manager\.store import (?!journal\b).*\b(Journal|JournalLine|EventKind"
    r"|JournalError|MissingJournalError|CorruptJournalError|JOURNAL_NAME)\b"
)


def test_no_caller_reaches_a_journal_name_through_the_store_package():
    # The opt-in tiers (e2e_fake, soak, e2e) never run in the default suite,
    # and `--collect-only` does not run test bodies, so a stale call through
    # the package there would only fail when someone runs that tier.
    assert _THROUGH_THE_PACKAGE.search("from agent_manager.store import JournalError, Store")
    assert _THROUGH_THE_PACKAGE.search("lines = store.Journal(run_id).read()")
    assert _THROUGH_THE_PACKAGE.search("except store_module.MissingJournalError:")
    assert not _THROUGH_THE_PACKAGE.search(
        "from agent_manager.store import journal as store_journal"
    )
    assert not _THROUGH_THE_PACKAGE.search(
        "from agent_manager.store.journal import JournalError"
    )
    assert not _THROUGH_THE_PACKAGE.search("lines = store_journal.Journal(run_id).read()")
    me = Path(__file__).resolve()
    hits = [
        f"{path.relative_to(_REPO)}:{number}: {line.strip()}"
        for root in (_REPO / "src", _REPO / "tests")
        for path in sorted(root.rglob("*.py"))
        if path.resolve() != me and "__pycache__" not in path.parts
        for number, line in enumerate(path.read_text().splitlines(), start=1)
        if _THROUGH_THE_PACKAGE.search(line)
    ]
    assert hits == []


_PRIVATE_JOURNAL_NAME = re.compile(
    r"\bstore_journal\._\w|from agent_manager\.store\.journal import .*\b_\w"
)


def test_no_source_module_reads_a_private_journal_name():
    # `_RETIRED_ATTEMPT_KEYS` and `_current_attempt_payload` live in `store.replay`
    # with their readers; moving them here would make `store.replay` read a
    # private name off this module.
    assert _PRIVATE_JOURNAL_NAME.search("store_journal._EVENT_KINDS")
    assert not _PRIVATE_JOURNAL_NAME.search("store_journal.Journal(run_id).read()")
    hits = [
        f"{path.relative_to(_REPO)}:{number}: {line.strip()}"
        for path in sorted((_REPO / "src").rglob("*.py"))
        if "__pycache__" not in path.parts
        for number, line in enumerate(path.read_text().splitlines(), start=1)
        if _PRIVATE_JOURNAL_NAME.search(line)
    ]
    assert hits == []


LINE_TS = "2026-09-23T10:00:00+00:00"


def _run_line(seq: int, i: object) -> dict:
    """A known `run_upsert` line of `RUN_ID` whose payload is `{"i": i}`."""
    return journal_line(RUN_ID, seq, LINE_TS, payload={"i": i})


def _journal_file(
    *records: dict, tail: str = "", run_id: str = RUN_ID
) -> store_journal.Journal:
    """The reader of `run_id`'s journal file, written first as `write_journal`
    writes it: one JSON line per record, then `tail` verbatim."""
    write_journal(run_id, records, tail=tail or None)
    return store_journal.Journal(run_id)


def test_constructing_a_journal_creates_and_reads_nothing(repo, tmp_path):
    journal = store_journal.Journal("never-ran")

    assert journal.run_id == "never-ran"
    assert journal.path == (
        paths.data_path() / "runs" / "never-ran" / store_journal.JOURNAL_NAME
    )
    assert not (tmp_path / "data").exists()

    corrupt = _journal_file(_run_line(1, 0), tail="this is not json\n")
    with pytest.raises(store_journal.CorruptJournalError) as excinfo:
        corrupt.read()
    assert f"{corrupt.path}:2:" in str(excinfo.value)


def test_a_journal_of_only_blank_lines_reads_as_empty(repo):
    # A crash between write and flush could leave blank lines and nothing
    # else. That is an empty journal, not a corrupt one.
    assert _journal_file(tail="\n\n").read() == []


def test_read_returns_lines_in_seq_order_not_file_order(repo):
    journal = _journal_file(_run_line(1, 1), _run_line(3, 3), _run_line(2, 2))

    lines = journal.read()

    assert [line.seq for line in lines] == [1, 2, 3]
    assert [line.payload["i"] for line in lines] == [1, 2, 3]


def test_reading_a_journal_that_does_not_exist_raises(repo):
    journal = store_journal.Journal("run-never-started")
    with pytest.raises(store_journal.MissingJournalError) as excinfo:
        journal.read()
    assert "run-never-started" in str(excinfo.value)
    assert str(journal.path) in str(excinfo.value)


def test_a_non_json_line_names_the_file_and_the_line_number(repo):
    journal = _journal_file(_run_line(1, 0), tail="this is not json\n")

    with pytest.raises(store_journal.CorruptJournalError) as excinfo:
        journal.read()
    message = str(excinfo.value)
    assert str(journal.path) in message
    assert ":2:" in message


def test_a_truncated_final_line_is_an_error_but_a_blank_one_is_not(repo):
    # A crash mid-append leaves either nothing, a blank line, or half a line.
    # The blank one is noise; the half line is data loss and says so.
    assert [line.payload["i"] for line in _journal_file(_run_line(1, 0), tail="\n").read()] == [0]

    journal = _journal_file(_run_line(1, 0), tail='\n{"seq": 2, "run_id": "run-2026\n')
    with pytest.raises(store_journal.CorruptJournalError) as excinfo:
        journal.read()
    assert ":3:" in str(excinfo.value)


def test_an_envelope_with_an_unknown_key_is_rejected(repo):
    journal = _journal_file(_run_line(1, 0), {**_run_line(2, 1), "operator": "someone"})

    with pytest.raises(ValidationError) as excinfo:
        journal.read()
    assert "operator" in str(excinfo.value)


def test_ignore_torn_tail_skips_only_an_unterminated_last_line(repo):
    journal = _journal_file(_run_line(1, 0), tail='{"seq": 2, "run_id"')

    with pytest.raises(store_journal.CorruptJournalError):
        journal.read()
    assert [line.payload["i"] for line in journal.read(ignore_torn_tail=True)] == [0]


def test_ignore_torn_tail_still_rejects_a_bad_line_before_the_last(repo):
    journal = _journal_file(_run_line(1, 0), tail='this is not json\n{"seq": 3')

    with pytest.raises(store_journal.CorruptJournalError) as excinfo:
        journal.read(ignore_torn_tail=True)
    assert ":2:" in str(excinfo.value)


def test_ignore_torn_tail_still_raises_for_a_missing_journal(repo):
    journal = store_journal.Journal("run-never-started")
    with pytest.raises(store_journal.MissingJournalError):
        journal.read(ignore_torn_tail=True)


def test_reading_a_journal_that_does_not_exist_creates_no_data_dir(repo, tmp_path):
    with pytest.raises(store_journal.MissingJournalError):
        store_journal.Journal("run-that-never-was").read()

    assert not (tmp_path / "data").exists()


# -- reading event kinds this version does not recognise (am-watch §3.4) ------
#
# Default suite, unmarked: real temp JSONL/SQLite files, no git, brd or
# subprocess. A newer `am` may journal an event kind this version's `EventKind`
# does not list; reading skips that line.

UNRECOGNISED_EVENT = "future_upsert"


def _unrecognised_line(seq: int, run_id: str = RUN_ID, **extra: object) -> dict:
    """A well-formed envelope whose `event` this version's `EventKind` lacks."""
    assert UNRECOGNISED_EVENT not in get_args(store_journal.EventKind)
    record: dict = {
        "seq": seq,
        "ts": "2026-10-02T10:00:00+00:00",
        "run_id": run_id,
        "event": UNRECOGNISED_EVENT,
        "payload": {"anything": "at all"},
    }
    record.update(extra)
    return record


def test_read_skips_a_line_whose_event_it_does_not_recognise(repo):
    journal = _journal_file(_run_line(1, 0), _unrecognised_line(2), _run_line(3, 2))

    lines = journal.read()

    assert [line.seq for line in lines] == [1, 3]
    assert [line.payload["i"] for line in lines] == [0, 2]
    assert all(line.event == "run_upsert" for line in lines)


def test_an_unrecognised_event_is_skipped_whatever_else_the_line_holds(repo):
    journal = _journal_file(
        _run_line(1, 0),
        _unrecognised_line(
            2,
            operator="someone",
            ts="not a timestamp",
            story=123,
            attempt="first",
            payload={"nested": [1, {"x": None}], "status": "whatever"},
        ),
    )

    assert [line.seq for line in journal.read()] == [1]


def test_an_unrecognised_event_without_a_valid_seq_still_raises(repo):
    # A skipped line must still carry a positive `seq`: a line with no usable
    # seq is not tolerated just because its event is unknown.
    without_seq = _unrecognised_line(2)
    del without_seq["seq"]

    with pytest.raises(ValidationError) as excinfo:
        _journal_file(_run_line(1, 0), without_seq).read()
    assert "seq" in str(excinfo.value)

    with pytest.raises(ValidationError) as excinfo:
        _journal_file(_unrecognised_line(0)).read()
    assert "seq" in str(excinfo.value)


@pytest.mark.parametrize(
    "raw",
    [
        '{"seq": 2, "ts": "2026-10-02T10:00:00+00:00", "run_id": "r", "payload": {}}',
        '{"seq": 2, "ts": "2026-10-02T10:00:00+00:00", "run_id": "r", "event": null, "payload": {}}',
        '{"seq": 2, "ts": "2026-10-02T10:00:00+00:00", "run_id": "r", "event": 5, "payload": {}}',
        '["future_upsert"]',
        "null",
    ],
    ids=["no-event", "null-event", "int-event", "array", "json-null"],
)
def test_a_line_without_a_string_event_or_not_an_object_still_raises(repo, raw):
    # Only a *string* event outside EventKind is skipped.
    journal = _journal_file(_run_line(1, 0), tail=raw + "\n")

    with pytest.raises(ValidationError):
        journal.read()


def test_read_passes_unknown_payload_keys_on_a_known_event_through(repo):
    # §3.4's "ignore unknown payload keys" holds at the read layer because
    # `payload` is an untyped dict; `replay()` still judges it.
    journal = _journal_file(
        _run_line(1, 0),
        {**_run_line(2, 1), "payload": {"i": 1, "future_key": {"deep": True}}},
    )

    assert journal.read()[1].payload == {"i": 1, "future_key": {"deep": True}}


def test_ignore_torn_tail_and_an_unrecognised_event_are_both_skipped(repo):
    journal = _journal_file(_run_line(1, 0), _unrecognised_line(2), tail='{"seq": 3, "run_id"')

    assert [line.payload["i"] for line in journal.read(ignore_torn_tail=True)] == [0]
    with pytest.raises(store_journal.CorruptJournalError) as excinfo:
        journal.read()
    assert ":3:" in str(excinfo.value)


# -- ts_text: how a line writes its ts -------------------------------------------


def test_ts_text_is_how_a_journal_line_writes_its_ts():
    # Review Focus 4: a whole-second reading has no fraction, and still round-trips.
    for ts, text in (
        (
            datetime(2026, 10, 7, 5, 48, 8, 123456, tzinfo=timezone.utc),
            "2026-10-07T05:48:08.123456Z",
        ),
        (datetime(2026, 10, 7, 5, 48, 8, tzinfo=timezone.utc), "2026-10-07T05:48:08Z"),
    ):
        line = store_journal.JournalLine(seq=1, ts=ts, run_id=RUN_ID, event="run_upsert")
        assert store_journal.ts_text(ts) == text == line.model_dump(mode="json")["ts"]
        assert (
            store_journal.JournalLine(seq=1, ts=text, run_id=RUN_ID, event="run_upsert")
            == line
        )


# -- read_verbatim: a journal as `am migrate` imports it (single-store 1.3.2 D4)
#
# Unit tier: real JSONL files under the test's `XDG_DATA_HOME`, nothing spawned.

VERBATIM_RUN = "run-a"
VERBATIM_TS = "2026-10-07T05:48:08.123+00:00"
_DROP = object()


def _raw(**changes: object) -> str:
    """A valid line of `VERBATIM_RUN` as JSON text, `changes` applied; `_DROP` removes a key."""
    record = journal_line(VERBATIM_RUN, 1, VERBATIM_TS)
    for key, value in changes.items():
        if value is _DROP:
            del record[key]
        else:
            record[key] = value
    return json.dumps(record)


def _first(seq: int = 1) -> dict[str, object]:
    return journal_line(VERBATIM_RUN, seq, VERBATIM_TS)


def test_unimportable_line_error_is_a_journal_error():
    assert issubclass(store_journal.UnimportableLineError, store_journal.JournalError)
    error = store_journal.UnimportableLineError(Path("/x/journal.jsonl"), 3, "why")
    assert (error.path, error.line, error.why, str(error)) == (
        Path("/x/journal.jsonl"), 3, "why", "/x/journal.jsonl:3: why"
    )


def test_read_verbatim_keeps_the_ts_string_and_maps_every_field():
    payload = {"status": "running", "ratio": 1.0, "name": "é"}
    path = write_journal(
        VERBATIM_RUN,
        [
            journal_line(
                VERBATIM_RUN, 1, VERBATIM_TS, "phase_upsert", payload,
                story="s1", card="c1", phase="spec", attempt=2,
            )
        ],
    )

    found = store_journal.read_verbatim(path, VERBATIM_RUN)

    assert found == store_journal.VerbatimJournal(
        run_id=VERBATIM_RUN,
        path=path,
        lines=(
            store_journal.VerbatimLine(
                line=1,
                run_seq=1,
                ts=VERBATIM_TS,
                kind="phase_upsert",
                story_id="s1",
                card_id="c1",
                phase="spec",
                attempt=2,
                payload=payload,
            ),
        ),
        torn_line=None,
    )
    assert type(found.lines[0].payload["ratio"]) is float


def test_read_verbatim_keeps_retired_keys_and_cancelled_untouched():
    run = {"status": models.LEGACY_CANCELED, "workflow": "task"}
    attempt = {"status": "done", "tokens_in": 10, "tokens_out": 20, "cost": 0.5}
    path = write_journal(
        VERBATIM_RUN,
        [
            journal_line(VERBATIM_RUN, 1, VERBATIM_TS, "run_upsert", run),
            journal_line(
                VERBATIM_RUN, 2, VERBATIM_TS, "attempt_upsert", attempt,
                story="s1", card="c1", phase="spec", attempt=1,
            ),
        ],
    )

    found = store_journal.read_verbatim(path, VERBATIM_RUN)

    assert [line.payload for line in found.lines] == [run, attempt]


def test_read_verbatim_skips_blank_lines():
    path = write_journal(VERBATIM_RUN, [_first()], tail="\n   \n" + _raw(seq=2) + "\n")

    found = store_journal.read_verbatim(path, VERBATIM_RUN)

    assert [(line.line, line.run_seq) for line in found.lines] == [(1, 1), (4, 2)]
    assert found.torn_line is None


def test_read_verbatim_skips_a_torn_tail_and_reports_its_line():
    path = write_journal(VERBATIM_RUN, [_first(1), _first(2)], tail='{"seq": 3, "ts')
    before = path.read_bytes()

    found = store_journal.read_verbatim(path, VERBATIM_RUN)

    assert [line.run_seq for line in found.lines] == [1, 2]
    assert found.torn_line == 3
    assert path.read_bytes() == before


def test_read_verbatim_imports_a_json_final_line_without_newline():
    path = write_journal(VERBATIM_RUN, [_first()], tail=_raw(seq=2))

    found = store_journal.read_verbatim(path, VERBATIM_RUN)

    assert [line.run_seq for line in found.lines] == [1, 2]
    assert found.torn_line is None


def test_read_verbatim_refuses_a_non_json_line_that_ends_in_newline():
    path = write_journal(VERBATIM_RUN, [_first()], tail="not json\n")

    with pytest.raises(store_journal.UnimportableLineError) as raised:
        store_journal.read_verbatim(path, VERBATIM_RUN)

    assert raised.value.path == path
    assert raised.value.line == 2
    assert str(raised.value).startswith(f"{path}:2: line is not JSON")


def test_read_verbatim_refuses_a_non_json_line_before_the_last():
    path = write_journal(VERBATIM_RUN, [_first()], tail="not json\n" + _raw(seq=3) + "\n")

    with pytest.raises(store_journal.UnimportableLineError) as raised:
        store_journal.read_verbatim(path, VERBATIM_RUN)

    assert raised.value.path == path
    assert raised.value.line == 2
    assert str(raised.value).startswith(f"{path}:2: line is not JSON")


@pytest.mark.parametrize(
    "texts, why",
    [
        (("[1, 2]",), "line is not a JSON object"),
        ((_raw(extra=1),), "unknown key 'extra'"),
        ((_raw(seq=_DROP),), "seq is missing"),
        ((_raw(seq=0),), "seq is 0, expected a positive integer"),
        ((_raw(seq=True),), "seq is True, expected a positive integer"),
        ((_raw(seq="1"),), "seq is '1', expected a positive integer"),
        ((_raw(), _raw()), "seq 1 repeats line 1"),
        ((_raw(ts=_DROP),), "ts is missing"),
        ((_raw(ts=5),), "ts is 5, expected a string"),
        ((_raw(ts="yesterday"),), "ts 'yesterday' is not an ISO-8601 timestamp"),
        ((_raw(ts="2026-10-07T05:48:08"),), "ts '2026-10-07T05:48:08' has no UTC offset"),
        ((_raw(run_id="run-b"),), "run_id is 'run-b', expected 'run-a'"),
        ((_raw(event=_DROP),), "event is missing"),
        ((_raw(event=""),), "event is '', expected a non-empty string"),
        ((_raw(event=5),), "event is 5, expected a non-empty string"),
        ((_raw(story=5),), "story is 5, expected a string or null"),
        ((_raw(attempt=True),), "attempt is True, expected an integer or null"),
        ((_raw(attempt="1"),), "attempt is '1', expected an integer or null"),
        ((_raw(payload=[1]),), "payload is a list, expected a JSON object"),
    ],
    ids=[
        "not an object",
        "unknown key",
        "seq missing",
        "seq zero",
        "seq true",
        "seq string",
        "seq repeated",
        "ts missing",
        "ts number",
        "ts unparseable",
        "ts naive",
        "run_id mismatch",
        "event missing",
        "event empty",
        "event number",
        "story number",
        "attempt true",
        "attempt string",
        "payload list",
    ],
)
def test_read_verbatim_refuses_a_malformed_envelope(texts, why):
    path = write_journal(VERBATIM_RUN, [], tail="".join(text + "\n" for text in texts))

    with pytest.raises(store_journal.UnimportableLineError) as raised:
        store_journal.read_verbatim(path, VERBATIM_RUN)

    assert raised.value.path == path
    assert raised.value.line == len(texts)
    assert str(raised.value) == f"{path}:{len(texts)}: {why}"


def test_read_verbatim_imports_an_unknown_event_kind():
    path = write_journal(
        VERBATIM_RUN, [journal_line(VERBATIM_RUN, 1, VERBATIM_TS, "future_upsert", {"x": 1})]
    )

    (line,) = store_journal.read_verbatim(path, VERBATIM_RUN).lines

    assert (line.kind, line.payload) == ("future_upsert", {"x": 1})


def test_read_verbatim_orders_lines_by_seq():
    path = write_journal(VERBATIM_RUN, [_first(2), _first(1)])

    found = store_journal.read_verbatim(path, VERBATIM_RUN)

    assert [(line.run_seq, line.line) for line in found.lines] == [(1, 2), (2, 1)]


def test_read_verbatim_of_an_empty_file_has_no_lines():
    path = write_journal(VERBATIM_RUN, [])

    assert store_journal.read_verbatim(path, VERBATIM_RUN) == store_journal.VerbatimJournal(
        run_id=VERBATIM_RUN, path=path, lines=(), torn_line=None
    )


def test_read_verbatim_of_a_missing_file_raises_missing_journal():
    path = paths.data_path() / "runs" / VERBATIM_RUN / store_journal.JOURNAL_NAME

    with pytest.raises(store_journal.MissingJournalError):
        store_journal.read_verbatim(path, VERBATIM_RUN)

    assert not paths.data_path().exists()


def test_read_verbatim_refuses_invalid_utf8_unless_it_is_the_torn_tail():
    path = write_journal(VERBATIM_RUN, [_first()])
    good = path.read_bytes()
    path.write_bytes(good + b"\xff\xfe\n")

    with pytest.raises(store_journal.UnimportableLineError) as raised:
        store_journal.read_verbatim(path, VERBATIM_RUN)

    assert raised.value.line == 2
    assert str(raised.value) == f"{path}:2: line is not UTF-8"

    path.write_bytes(good + b"\xff\xfe")

    found = store_journal.read_verbatim(path, VERBATIM_RUN)

    assert [line.run_seq for line in found.lines] == [1]
    assert found.torn_line == 2
