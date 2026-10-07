"""Behaviour of `agent_manager.store.journal`: a run's append-only JSONL journal,
its line envelope and event kinds, appending and reading it.

Real JSONL files under `tmp_path`, and threads in one process; nothing spawns a
process, so these are unit tests. The `repo` fixture redirects `XDG_DATA_HOME`
and `HOME`.
"""

import ast
import json
import re
import sys
import threading
from pathlib import Path
from typing import get_args

import pytest
from pydantic import ValidationError

from agent_manager import paths, store
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
    "_UnknownEventLine",
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
    }
    assert store_journal.JOURNAL_NAME == "journal.jsonl"


def test_the_store_package_does_not_re_export_journal_names():
    assert [name for name in _JOURNAL_NAMES if hasattr(store, name)] == []
    # `append` fsyncs through `store_journal.os`. Were `os` still bound on the
    # package, a test patching `store.os.fsync` would patch the shared module
    # and pass while naming the wrong one.
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
    assert not _PRIVATE_JOURNAL_NAME.search("store_journal.Journal._for_reading(run_id)")
    hits = [
        f"{path.relative_to(_REPO)}:{number}: {line.strip()}"
        for path in sorted((_REPO / "src").rglob("*.py"))
        if "__pycache__" not in path.parts
        for number, line in enumerate(path.read_text().splitlines(), start=1)
        if _PRIVATE_JOURNAL_NAME.search(line)
    ]
    assert hits == []


def _append_raw(journal: store_journal.Journal, record: dict) -> None:
    with journal.path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")


def test_append_writes_one_json_line_with_every_coordinate(repo):
    journal = store_journal.Journal(RUN_ID)
    line = journal.append(
        "attempt_upsert",
        {"n": 1, "status": "started"},
        story="8831189b",
        card="ef248597",
        phase="implement",
        attempt=1,
    )

    assert journal.path == paths.run_dir(RUN_ID) / "journal.jsonl"
    text = journal.path.read_text(encoding="utf-8")
    assert text.endswith("\n")
    assert len(text.splitlines()) == 1

    record = json.loads(text)
    assert record["seq"] == 1
    assert record["run_id"] == RUN_ID
    assert record["event"] == "attempt_upsert"
    assert record["story"] == "8831189b"
    assert record["card"] == "ef248597"
    assert record["phase"] == "implement"
    assert record["attempt"] == 1
    assert record["payload"] == {"n": 1, "status": "started"}
    assert line.seq == 1


def test_run_level_lines_leave_the_lower_coordinates_null(repo):
    journal = store_journal.Journal(RUN_ID)
    journal.append("run_upsert", {"status": "started"})

    record = json.loads(journal.path.read_text(encoding="utf-8"))
    assert record["story"] is None
    assert record["card"] is None
    assert record["phase"] is None
    assert record["attempt"] is None


def test_sequence_numbers_increase_by_one(repo):
    journal = store_journal.Journal(RUN_ID)
    seqs = [journal.append("run_upsert", {"i": i}).seq for i in range(3)]
    assert seqs == [1, 2, 3]
    assert [line.seq for line in journal.read()] == [1, 2, 3]


def test_a_reopened_journal_continues_the_sequence(repo):
    first = store_journal.Journal(RUN_ID)
    first.append("run_upsert", {"i": 0})
    first.append("run_upsert", {"i": 1})

    second = store_journal.Journal(RUN_ID)
    assert second.last_seq() == 2
    assert second.append("run_upsert", {"i": 2}).seq == 3
    assert [line.payload["i"] for line in second.read()] == [0, 1, 2]


def test_appending_reads_nothing_from_disk_once_the_journal_is_open(repo, monkeypatch):
    # P2: one process writes a run, so the counter is read once, at open, and
    # appending never re-reads the file. Counted, not timed (P7).
    calls = {"read": 0, "last_seq": 0}
    real_read = store_journal.Journal.read
    real_last_seq = store_journal.Journal.last_seq

    def counting_read(self):
        calls["read"] += 1
        return real_read(self)

    def counting_last_seq(self):
        calls["last_seq"] += 1
        return real_last_seq(self)

    monkeypatch.setattr(store_journal.Journal, "read", counting_read)
    monkeypatch.setattr(store_journal.Journal, "last_seq", counting_last_seq)

    journal = store_journal.Journal(RUN_ID)
    calls["read"] = 0
    calls["last_seq"] = 0

    seqs = [journal.append("run_upsert", {"i": i}).seq for i in range(50)]

    assert calls == {"read": 0, "last_seq": 0}
    assert seqs == list(range(1, 51))


def test_a_fresh_journal_on_an_existing_run_continues_after_the_last_line(repo):
    first = store_journal.Journal(RUN_ID)
    for i in range(3):
        first.append("run_upsert", {"i": i})

    resumed = store_journal.Journal(RUN_ID)
    assert resumed.append("run_upsert", {"i": 3}).seq == 4
    assert resumed.append("run_upsert", {"i": 4}).seq == 5
    assert [line.seq for line in resumed.read()] == [1, 2, 3, 4, 5]


def test_a_journal_of_only_blank_lines_opens_at_zero(repo):
    # Review Focus 1: a crash between write and flush can leave blank lines
    # and nothing else. That is an empty journal, not a corrupt one.
    first = store_journal.Journal(RUN_ID)
    first.path.write_text("\n\n", encoding="utf-8")

    journal = store_journal.Journal(RUN_ID)
    assert journal.append("run_upsert", {"i": 0}).seq == 1


def test_a_journal_opened_on_out_of_order_lines_continues_after_the_highest(repo):
    # Review Focus 2: the counter is the highest seq on disk, not the seq of
    # the last line in file order.
    first = store_journal.Journal(RUN_ID)
    first.append("run_upsert", {"i": 1})
    for seq in (3, 2):
        _append_raw(
            first,
            {
                "seq": seq,
                "ts": "2026-09-23T10:00:00+00:00",
                "run_id": RUN_ID,
                "event": "run_upsert",
                "payload": {"i": seq},
            },
        )

    journal = store_journal.Journal(RUN_ID)
    assert journal.append("run_upsert", {"i": 4}).seq == 4
    assert [line.seq for line in journal.read()] == [1, 2, 3, 4]


def test_opening_a_corrupt_journal_raises_at_open(repo):
    # The counter is read at open, so a corrupt journal is reported there
    # rather than at the first append.
    first = store_journal.Journal(RUN_ID)
    first.append("run_upsert", {"i": 0})
    with first.path.open("a", encoding="utf-8") as handle:
        handle.write("this is not json\n")

    with pytest.raises(store_journal.CorruptJournalError) as excinfo:
        store_journal.Journal(RUN_ID)
    message = str(excinfo.value)
    assert str(first.path) in message
    assert ":2:" in message


def test_reading_a_journal_that_does_not_exist_raises(repo):
    journal = store_journal.Journal("run-never-started")
    with pytest.raises(store_journal.MissingJournalError) as excinfo:
        journal.read()
    assert "run-never-started" in str(excinfo.value)
    assert str(journal.path) in str(excinfo.value)


def test_append_holds_the_lock_across_the_write_and_the_fsync(repo, monkeypatch):
    # Deterministic (P7): rather than racing threads and hoping to catch an
    # overlap, check the lock is held at the moment the line is fsynced.
    journal = store_journal.Journal(RUN_ID)
    held_during_fsync: list[bool] = []
    real_fsync = store_journal.os.fsync

    def spying_fsync(fd):
        held_during_fsync.append(journal._lock.locked())
        real_fsync(fd)

    monkeypatch.setattr(store_journal.os, "fsync", spying_fsync)

    journal.append("run_upsert", {"i": 0})
    journal.append("run_upsert", {"i": 1})

    assert held_during_fsync == [True, True]
    assert journal._lock.locked() is False


def test_eight_threads_sharing_one_journal_write_800_whole_lines_numbered_1_to_800(repo):
    workers, per_worker = 8, 100
    journal = store_journal.Journal(RUN_ID)
    start = threading.Barrier(workers)
    errors: list[BaseException] = []

    def work(worker: int) -> None:
        try:
            start.wait()
            for i in range(per_worker):
                journal.append("run_upsert", {"w": worker, "i": i})
        except BaseException as error:  # surfaced by the assertion below
            errors.append(error)

    threads = [threading.Thread(target=work, args=(w,)) for w in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    raw = [
        text
        for text in journal.path.read_text(encoding="utf-8").splitlines()
        if text.strip()
    ]
    assert len(raw) == workers * per_worker
    records = [json.loads(text) for text in raw]  # a torn line fails here
    assert sorted(record["seq"] for record in records) == list(
        range(1, workers * per_worker + 1)
    )
    for worker in range(workers):
        own = [record for record in records if record["payload"]["w"] == worker]
        assert [record["payload"]["i"] for record in sorted(own, key=lambda r: r["seq"])] == list(
            range(per_worker)
        )


def test_a_failed_write_releases_the_lock_and_does_not_spend_a_number(repo, tmp_path):
    # Review Focus 3: the path cannot be opened for appending, so nothing
    # lands on disk. The lock is released and the next append reuses the number.
    journal = store_journal.Journal(RUN_ID)
    journal.append("run_upsert", {"i": 0})

    real_path = journal.path
    directory = tmp_path / "a-directory-not-a-file"
    directory.mkdir()
    journal.path = directory
    with pytest.raises(OSError):
        journal.append("run_upsert", {"i": "lost"})
    assert journal._lock.locked() is False

    journal.path = real_path
    assert journal.append("run_upsert", {"i": 1}).seq == 2
    assert [line.seq for line in journal.read()] == [1, 2]


def test_a_failed_fsync_after_the_write_spends_the_number_so_no_seq_repeats(repo, monkeypatch):
    # Once write and flush succeed the line is in the file, fsynced or not, so
    # retrying its number would put a duplicate seq on disk.
    journal = store_journal.Journal(RUN_ID)
    journal.append("run_upsert", {"i": 0})

    real_fsync = store_journal.os.fsync

    def failing_fsync(fd):
        raise OSError("fsync failed")

    monkeypatch.setattr(store_journal.os, "fsync", failing_fsync)
    with pytest.raises(OSError, match="fsync failed"):
        journal.append("run_upsert", {"i": "unsynced"})
    assert journal._lock.locked() is False

    monkeypatch.setattr(store_journal.os, "fsync", real_fsync)
    assert journal.append("run_upsert", {"i": 2}).seq == 3
    assert [line.seq for line in journal.read()] == [1, 2, 3]


def test_an_invalid_line_releases_the_lock_and_does_not_spend_a_number(repo):
    # Review Focus 4: JournalLine validation runs inside the lock.
    journal = store_journal.Journal(RUN_ID)
    journal.append("run_upsert", {"i": 0})

    with pytest.raises(ValidationError):
        journal.append("not_an_event", {})  # type: ignore[arg-type]
    assert journal._lock.locked() is False

    assert journal.append("run_upsert", {"i": 1}).seq == 2
    assert [line.seq for line in journal.read()] == [1, 2]


def test_a_non_json_line_names_the_file_and_the_line_number(repo):
    journal = store_journal.Journal(RUN_ID)
    journal.append("run_upsert", {"i": 0})
    with journal.path.open("a", encoding="utf-8") as handle:
        handle.write("this is not json\n")

    with pytest.raises(store_journal.CorruptJournalError) as excinfo:
        journal.read()
    message = str(excinfo.value)
    assert str(journal.path) in message
    assert ":2:" in message


def test_a_truncated_final_line_is_an_error_but_a_blank_one_is_not(repo):
    # Review Focus 2: a crash mid-append leaves either nothing, a blank line, or
    # half a line. The blank one is noise; the half line is data loss and says so.
    journal = store_journal.Journal(RUN_ID)
    journal.append("run_upsert", {"i": 0})
    with journal.path.open("a", encoding="utf-8") as handle:
        handle.write("\n")
    assert [line.payload["i"] for line in journal.read()] == [0]

    with journal.path.open("a", encoding="utf-8") as handle:
        handle.write('{"seq": 2, "run_id": "run-2026\n')
    with pytest.raises(store_journal.CorruptJournalError) as excinfo:
        journal.read()
    assert ":3:" in str(excinfo.value)


def test_an_envelope_with_an_unknown_key_is_rejected(repo):
    journal = store_journal.Journal(RUN_ID)
    journal.append("run_upsert", {"i": 0})
    with journal.path.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "seq": 2,
                    "ts": "2026-09-23T10:00:00+00:00",
                    "run_id": RUN_ID,
                    "event": "run_upsert",
                    "payload": {},
                    "operator": "someone",
                }
            )
            + "\n"
        )

    with pytest.raises(ValidationError) as excinfo:
        journal.read()
    assert "operator" in str(excinfo.value)


def test_a_run_directory_that_cannot_be_created_propagates_the_os_error(repo, tmp_path):
    # The journal defers directory creation to `paths.run_dir`, so the OS error
    # comes through untouched -- the store adds no fallback of its own.
    runs = tmp_path / "data" / "agent-manager" / "runs"
    runs.mkdir(parents=True)
    (runs / "run-blocked").write_text("not a directory")

    with pytest.raises(OSError):
        store_journal.Journal("run-blocked")


def test_ignore_torn_tail_skips_only_an_unterminated_last_line(repo):
    journal = store_journal.Journal(RUN_ID)
    journal.append("run_upsert", {"i": 0})
    with journal.path.open("a", encoding="utf-8") as handle:
        handle.write('{"seq": 2, "run_id"')

    with pytest.raises(store_journal.CorruptJournalError):
        journal.read()
    assert [line.payload["i"] for line in journal.read(ignore_torn_tail=True)] == [0]


def test_ignore_torn_tail_still_rejects_a_bad_line_before_the_last(repo):
    journal = store_journal.Journal(RUN_ID)
    journal.append("run_upsert", {"i": 0})
    with journal.path.open("a", encoding="utf-8") as handle:
        handle.write("this is not json\n")
        handle.write('{"seq": 3')

    with pytest.raises(store_journal.CorruptJournalError) as excinfo:
        journal.read(ignore_torn_tail=True)
    assert ":2:" in str(excinfo.value)


def test_ignore_torn_tail_still_raises_for_a_missing_journal(repo):
    journal = store_journal.Journal("run-never-started")
    with pytest.raises(store_journal.MissingJournalError):
        journal.read(ignore_torn_tail=True)


def test_reading_a_journal_that_does_not_exist_creates_no_data_dir(repo, tmp_path):
    with pytest.raises(store_journal.MissingJournalError):
        store_journal.Journal._for_reading("run-that-never-was").read()

    assert not (tmp_path / "data").exists()


# -- reading event kinds this version does not recognise (am-watch §3.4) ------
#
# Default suite, unmarked: real temp JSONL/SQLite files, no git, brd or
# subprocess. A newer `am` may journal an event kind this version's `EventKind`
# does not list; reading skips that line, writing stays strict.

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
    journal = store_journal.Journal(RUN_ID)
    journal.append("run_upsert", {"i": 0})
    _append_raw(journal, _unrecognised_line(2))
    _append_raw(
        journal,
        {
            "seq": 3,
            "ts": "2026-10-02T10:01:00+00:00",
            "run_id": RUN_ID,
            "event": "run_upsert",
            "payload": {"i": 2},
        },
    )

    lines = journal.read()

    assert [line.seq for line in lines] == [1, 3]
    assert [line.payload["i"] for line in lines] == [0, 2]
    assert all(line.event == "run_upsert" for line in lines)


def test_an_unrecognised_event_is_skipped_whatever_else_the_line_holds(repo):
    journal = store_journal.Journal(RUN_ID)
    journal.append("run_upsert", {"i": 0})
    _append_raw(
        journal,
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
    # Review Focus 1: `last_seq` must recover a skipped line's seq, so a line
    # with no usable seq is not tolerated just because its event is unknown.
    journal = store_journal.Journal(RUN_ID)
    journal.append("run_upsert", {"i": 0})
    without_seq = _unrecognised_line(2)
    del without_seq["seq"]
    _append_raw(journal, without_seq)

    with pytest.raises(ValidationError) as excinfo:
        journal.read()
    assert "seq" in str(excinfo.value)

    journal.path.write_text("", encoding="utf-8")
    _append_raw(journal, _unrecognised_line(0))
    with pytest.raises(ValidationError) as excinfo:
        journal.read()
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
    # Review Focus 2: only a *string* event outside EventKind is skipped.
    journal = store_journal.Journal(RUN_ID)
    journal.append("run_upsert", {"i": 0})
    with journal.path.open("a", encoding="utf-8") as handle:
        handle.write(raw + "\n")

    with pytest.raises(ValidationError):
        journal.read()


def test_read_passes_unknown_payload_keys_on_a_known_event_through(repo):
    # Review Focus 4: §3.4's "ignore unknown payload keys" holds at the read
    # layer because `payload` is an untyped dict; `replay()` still judges it.
    journal = store_journal.Journal(RUN_ID)
    journal.append("run_upsert", {"i": 0})
    _append_raw(
        journal,
        {
            "seq": 2,
            "ts": "2026-10-02T10:00:00+00:00",
            "run_id": RUN_ID,
            "event": "run_upsert",
            "payload": {"i": 1, "future_key": {"deep": True}},
        },
    )

    assert journal.read()[1].payload == {"i": 1, "future_key": {"deep": True}}


def test_ignore_torn_tail_and_an_unrecognised_event_are_both_skipped(repo):
    # Review Focus 3.
    journal = store_journal.Journal(RUN_ID)
    journal.append("run_upsert", {"i": 0})
    _append_raw(journal, _unrecognised_line(2))
    with journal.path.open("a", encoding="utf-8") as handle:
        handle.write('{"seq": 3, "run_id"')

    assert [line.payload["i"] for line in journal.read(ignore_torn_tail=True)] == [0]
    with pytest.raises(store_journal.CorruptJournalError) as excinfo:
        journal.read()
    assert ":3:" in str(excinfo.value)


def test_append_still_refuses_an_unrecognised_event(repo):
    journal = store_journal.Journal(RUN_ID)
    journal.append("run_upsert", {"i": 0})
    _append_raw(journal, _unrecognised_line(2))
    before = journal.path.read_bytes()

    with pytest.raises(ValidationError):
        journal.append(UNRECOGNISED_EVENT, {})  # type: ignore[arg-type]

    assert journal.path.read_bytes() == before
    assert journal._lock.locked() is False


def test_a_skipped_line_still_counts_toward_last_seq(repo):
    # `append` promises no seq is ever repeated on disk: an older `am` resuming
    # a newer `am`'s run must number its next line above the skipped one.
    first = store_journal.Journal(RUN_ID)
    first.append("run_upsert", {"i": 0})
    _append_raw(first, _unrecognised_line(2))

    reopened = store_journal.Journal(RUN_ID)
    assert reopened.last_seq() == 2
    assert reopened.append("run_upsert", {"i": 1}).seq == 3

    again = store_journal.Journal(RUN_ID)
    assert again.last_seq() == 3
    assert [line.seq for line in again.read()] == [1, 3]


def test_reseek_counts_a_skipped_line(repo):
    # Review Focus 5: a lease take-over re-reads the highest seq on disk.
    journal = store_journal.Journal(RUN_ID)
    journal.append("run_upsert", {"i": 0})
    _append_raw(journal, _unrecognised_line(2))

    journal.reseek()

    assert journal.append("run_upsert", {"i": 1}).seq == 3


def test_read_holds_the_append_lock_across_the_scan(repo, monkeypatch):
    # Deterministic: check the lock is held at the moment the file is scanned,
    # so an append on another thread can never be met half-written.
    journal = store_journal.Journal(RUN_ID)
    journal.append("run_upsert", {"i": 0})
    held_during_scan: list[bool] = []
    real_scan = journal._scan

    def spying_scan(**kwargs):
        held_during_scan.append(journal._lock.locked())
        return real_scan(**kwargs)

    monkeypatch.setattr(journal, "_scan", spying_scan)

    lines = journal.read()

    assert held_during_scan == [True]
    assert journal._lock.locked() is False
    assert [line.seq for line in lines] == [1]
