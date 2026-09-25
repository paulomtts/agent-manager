<!-- task-pipeline: validated -->
# Give the journal a cached sequence number and a write lock (card 1432e5cc)

Milestone cdbfa10d, story db70e86b "Make the store safe to share between threads". Narrows decision P2 of `docs/superpowers/specs/2026-09-24-parallel-stories-design.md` (L50-56): one process writes a given run, and its threads share one `Journal`.

## Scope

Only `class Journal` in `src/agent_manager/store.py` (L151-L~220) and its tests in `tests/test_store.py`.

Out of scope, owned by sibling 5657f0d4 "Make the SQLite projection thread-safe": `open_db` (`check_same_thread=False`, `busy_timeout`, WAL), the reentrant lock on `Store` around `record_*` and `Store.close`, and the 8-thread `record_*` stress test comparing `rebuild_from_journal` with `load_run`. Also out of scope: multi-process writers, Integrate, per-story readiness, milestone-aware `am resume`, watch/retry/cancel, cost capture, reviewer Plan-Hash brief, Ctrl-C (addendum section 5).

## Observable behaviour

- `Journal.__init__(run_id)` reads the highest sequence number on disk once, when the journal is opened (`journal.jsonl` under `paths.run_dir(run_id)`; a missing file counts as 0). It keeps that number in memory along with a `threading.Lock`.
- `Journal.append(...)` holds the lock while it assigns `cached + 1`, builds the `JournalLine`, writes the line, flushes, and calls `os.fsync`. It advances the cached counter only when the write succeeds. It no longer calls `last_seq()` or `read()`, so appending does not re-read the file. The docstring must stop saying that a second writer continues the sequence. Instead it should say that one process writes a given run, the counter is cached when the journal is opened, and the lock serialises the threads in that process.
- `last_seq()` and `read()` stay unchanged and still read from disk. Existing tests call `last_seq()` after appends, and `Store.rebuild_from_journal` reads through `read()`. When the run id differs from the store's own, it builds a second `Journal` and only reads from it, so it does not write through a second instance.
- A resumed run works as before. When `Store.open` (about L468) opens a run that already has lines, the new `Journal` continues from the last sequence number.
- `JournalLine` (Pydantic, `extra=forbid`, `seq gt=0`), the line format, the rule that the journal is appended before the row, and "journal wins on disagreement" all stay unchanged. The CLI and JSON envelope are not affected.

## Error paths

- If `__init__` finds a corrupt existing journal, it raises `CorruptJournalError`, as `last_seq()` does today. This error now happens when the journal is opened, not at the first append.
- If the write or fsync fails inside `append`, the exception propagates and the lock is released. The cached counter does not advance, so the next append retries the same number.

## Deliberate change: an obsolete test

`test_two_writers_on_one_run_never_reuse_a_sequence_number` (`tests/test_store.py` L162-173) creates two `Journal` objects on one run before any append and expects the sequence [1,2,3]. Its comment names the old "derived from disk at append time, not cached" behaviour. With the counter cached, both instances start at 0 and the test must fail. Reading the counter lazily at the first append would also fail it. This test contradicts P2 and this card, so this card removes it on purpose, and the removal must be reported as a behaviour change. Later stages must not keep it passing by re-reading the disk on each append, because that defeats the card. The other existing store tests, including `test_a_reopened_journal_continues_the_sequence` (L151), must pass unchanged.

## Tests

Test-placement rule: `CLAUDE.md` says tests mirror `src/` under `tests/`, and main spec section 14 unit-tests store and file logic against temp dirs, keeping e2e as a separate opt-in tier. P7 requires determinism, not timing. All the tests below therefore go in `tests/test_store.py` (the flat mirror of `store.py`) and use the existing `repo` fixture and `RUN_ID`. None go in `tests/e2e` or a new tier.

1. `tests/test_store.py`: 8 threads each append 100 times through one shared `Journal`. The file has exactly 800 non-blank lines, each line passes `json.loads` (no interleaved writes), and the sequence numbers are exactly {1..800}, with no duplicates or gaps.
2. `tests/test_store.py`: use monkeypatch to count calls to `Journal.read` and `Journal.last_seq` (or file opens for reading). After the journal is constructed, appending N lines (for example N=50) makes zero further reads. The test counts calls and does not time anything.
3. `tests/test_store.py`: append some lines, construct a fresh `Journal(RUN_ID)` without calling `last_seq()` first, and check that its first append gets `last + 1` and the file's sequence numbers stay contiguous.
4. Remove `test_two_writers_on_one_run_never_reuse_a_sequence_number` as described above.

Verification: the whole default suite, including `tests/e2e`, passes under `uv run pytest`, and `--max-concurrent 1` behaviour is unchanged.

---

# Journal Cached Sequence Number and Write Lock Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `store.Journal` read the highest sequence number from disk once, when it is opened, and serialise appends from threads in one process behind a `threading.Lock`, so appending never re-reads the file and never reuses or skips a number.

**Architecture:** `Journal.__init__` sets `self._lock = threading.Lock()` and `self._seq = self.last_seq()`. `Journal.append` holds `self._lock` across numbering, building the `JournalLine`, writing, flushing and fsyncing, and sets `self._seq` only after the fsync returns. `last_seq()` and `read()` are unchanged disk readers. Nothing outside `class Journal` changes, and `Store` keeps constructing one `Journal` per `Store.open`.

**Tech Stack:** Python 3, stdlib `threading`, Pydantic (`JournalLine`), pytest, `uv`.

**Spec:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m4/task-give-the-journal-a-1432e5cc/docs/superpowers/specs/task-give-the-journal-a-1432e5cc-design.md` (prepended above, verbatim).

Worktree: `/home/paulomtts/Code/agent-manager/.claude/worktrees/m4/task-give-the-journal-a-1432e5cc`, branch `m4/task-give-the-journal-a-1432e5cc`, cut fresh from master. The plan assumes no code from sibling 5657f0d4 (SQLite thread-safety) or any other subtask exists on this branch. All paths below are relative to that worktree root, and every command runs from it.

## Global Constraints

- Only `class Journal` in `src/agent_manager/store.py` changes. Do not touch `open_db`, `Store`, `record_*`, `Store.close`, or `rebuild_from_journal` (sibling 5657f0d4 owns the SQLite and `Store` locking).
- One process writes a given run (P2). No multi-process writer support, no file locking, no re-reading the disk on each append.
- `last_seq()` and `read()` keep their current bodies and still read from disk.
- `JournalLine` (`extra="forbid"`, `seq: int = Field(gt=0)`), the JSON line format (`json.dumps(line.model_dump(mode="json"), sort_keys=True)` plus `"\n"`), journal-before-row ordering and "journal wins" are unchanged.
- Tests go in `tests/test_store.py` only, using the existing `repo` fixture (L27-34) and `RUN_ID` (L24). Nothing goes in `tests/e2e` or a new tier.
- Concurrency is proven deterministically (P7): barriers and lock-state assertions, never sleeps or timing.
- Verification is `uv run pytest` only (no lint or typecheck). The whole default suite, including `tests/e2e`, must pass.
- `test_two_writers_on_one_run_never_reuse_a_sequence_number` is deleted as a deliberate behaviour change and reported as such. No other existing test is edited.

## Review Focus

1. A journal file that exists but holds only blank lines (a crash between write and flush) is opened: the cached counter must be 0 and the first append must get seq 1. Test in Task 1.
2. A journal whose lines sit on disk out of seq order (for example 1, 3, 2) is opened: the next append must get max+1 (4), not last-line+1 (3). Test in Task 1.
3. A write that raises inside `append` (the path cannot be opened for appending): the lock must be released and the number must not be spent, so the next append reuses it. Test in Task 2.
4. An `append` whose `JournalLine` fails validation (an unknown event kind) inside the lock: the lock must be released and the counter must not move. Test in Task 2.
5. A resumed run opened through `Store.open` (not a bare `Journal`): its first `record_*` must continue the sequence. Test in Task 1.

Open question for the reviewer, not pinned by any test: the spec says the counter does not advance when fsync fails. If `write` and `flush` succeed but `os.fsync` raises, the line is already in the file, so retrying the same number would put a duplicate seq on disk. This plan implements the spec as written (the counter is set only after fsync returns). If the reviewer wants that case to advance the counter instead, it is a one-line move of `self._seq = seq` to just after `handle.flush()`.

## File Structure

- Modify: `src/agent_manager/store.py` — add `import threading` (imports at L14-24); change `class Journal` (L151-221): class docstring, `__init__` (L154-156), `append` (L189-221).
- Modify: `tests/test_store.py` — add `import threading` (imports at L14-22); delete L162-173; add new tests right after `test_a_reopened_journal_continues_the_sequence` (ends L159).

---

### Task 1: Cache the sequence number when the journal is opened

**Files:**
- Modify: `src/agent_manager/store.py:151-221`
- Test: `tests/test_store.py` (delete L162-173, add tests after L159)

**Interfaces:**
- Consumes: existing `store.Journal(run_id: str)`, `Journal.last_seq() -> int`, `Journal.read() -> list[JournalLine]`, `Journal.append(event, payload, *, story=None, card=None, phase=None, attempt=None) -> JournalLine`, `store.Store.open(root: Path, run_id: str) -> Store`, `Store.record_run(run: models.Run) -> JournalLine`, `store.CorruptJournalError`; test helpers `_run(repo, run_id=RUN_ID)` and `_append_raw(journal, record)` (defined at L634, later in the module; resolved at call time, so tests above it may call it).
- Produces: `Journal._seq: int` (the cached highest seq on disk as of open plus the lines this instance appended). Task 2 wraps the use of `_seq` in a lock.

- [ ] **Step 1: Delete the obsolete two-writers test**

In `tests/test_store.py`, delete this whole function (L162-173) and the blank lines after it, so `test_a_reopened_journal_continues_the_sequence` is followed directly by `test_reading_a_journal_that_does_not_exist_raises`:

```python
def test_two_writers_on_one_run_never_reuse_a_sequence_number(repo):
    # Review Focus 3: the sequence is derived from what is on disk at append
    # time, not cached at construction, so a second writer cannot collide.
    first = store.Journal(RUN_ID)
    second = store.Journal(RUN_ID)
    seqs = [
        first.append("run_upsert", {"w": "a"}).seq,
        second.append("run_upsert", {"w": "b"}).seq,
        first.append("run_upsert", {"w": "a"}).seq,
    ]
    assert seqs == [1, 2, 3]
    assert len({line.seq for line in first.read()}) == 3
```

This is the deliberate behaviour change from the spec: two `Journal` objects on one run are no longer supported (P2: one process, one shared `Journal`). Do not replace it with anything that re-reads the disk per append.

- [ ] **Step 2: Write the failing tests**

In `tests/test_store.py`, insert after `test_a_reopened_journal_continues_the_sequence` (the function ending at L159):

```python
def test_appending_reads_nothing_from_disk_once_the_journal_is_open(repo, monkeypatch):
    # P2: one process writes a run, so the counter is read once, at open, and
    # appending never re-reads the file. Counted, not timed (P7).
    calls = {"read": 0, "last_seq": 0}
    real_read = store.Journal.read
    real_last_seq = store.Journal.last_seq

    def counting_read(self):
        calls["read"] += 1
        return real_read(self)

    def counting_last_seq(self):
        calls["last_seq"] += 1
        return real_last_seq(self)

    monkeypatch.setattr(store.Journal, "read", counting_read)
    monkeypatch.setattr(store.Journal, "last_seq", counting_last_seq)

    journal = store.Journal(RUN_ID)
    calls["read"] = 0
    calls["last_seq"] = 0

    seqs = [journal.append("run_upsert", {"i": i}).seq for i in range(50)]

    assert calls == {"read": 0, "last_seq": 0}
    assert seqs == list(range(1, 51))


def test_a_fresh_journal_on_an_existing_run_continues_after_the_last_line(repo):
    first = store.Journal(RUN_ID)
    for i in range(3):
        first.append("run_upsert", {"i": i})

    resumed = store.Journal(RUN_ID)
    assert resumed.append("run_upsert", {"i": 3}).seq == 4
    assert resumed.append("run_upsert", {"i": 4}).seq == 5
    assert [line.seq for line in resumed.read()] == [1, 2, 3, 4, 5]


def test_a_resumed_store_continues_the_journal_sequence(repo):
    # Review Focus 5: `Store.open` builds the journal, so a resumed run
    # numbers its next record after the last line already on disk.
    st = store.Store.open(repo, RUN_ID)
    try:
        st.record_run(_run(repo))
    finally:
        st.close()

    reopened = store.Store.open(repo, RUN_ID)
    try:
        line = reopened.record_run(_run(repo).model_copy(update={"status": "done"}))
    finally:
        reopened.close()

    assert line.seq == 2
    assert [line.seq for line in store.Journal(RUN_ID).read()] == [1, 2]


def test_a_journal_of_only_blank_lines_opens_at_zero(repo):
    # Review Focus 1: a crash between write and flush can leave blank lines
    # and nothing else. That is an empty journal, not a corrupt one.
    first = store.Journal(RUN_ID)
    first.path.write_text("\n\n", encoding="utf-8")

    journal = store.Journal(RUN_ID)
    assert journal.append("run_upsert", {"i": 0}).seq == 1


def test_a_journal_opened_on_out_of_order_lines_continues_after_the_highest(repo):
    # Review Focus 2: the counter is the highest seq on disk, not the seq of
    # the last line in file order.
    first = store.Journal(RUN_ID)
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

    journal = store.Journal(RUN_ID)
    assert journal.append("run_upsert", {"i": 4}).seq == 4
    assert [line.seq for line in journal.read()] == [1, 2, 3, 4]


def test_opening_a_corrupt_journal_raises_at_open(repo):
    # The counter is read at open, so a corrupt journal is reported there
    # rather than at the first append.
    first = store.Journal(RUN_ID)
    first.append("run_upsert", {"i": 0})
    with first.path.open("a", encoding="utf-8") as handle:
        handle.write("this is not json\n")

    with pytest.raises(store.CorruptJournalError) as excinfo:
        store.Journal(RUN_ID)
    message = str(excinfo.value)
    assert str(first.path) in message
    assert ":2:" in message
```

- [ ] **Step 3: Run the new tests to verify the right ones fail**

Run: `uv run pytest tests/test_store.py -v -k "reads_nothing_from_disk or continues_after_the_last_line or resumed_store_continues or only_blank_lines or out_of_order_lines or corrupt_journal_raises_at_open"`

Expected:
- `test_appending_reads_nothing_from_disk_once_the_journal_is_open` FAILS: the assertion shows `calls` with `read` and `last_seq` near 50, because `append` currently calls `self.last_seq()` on every line.
- `test_opening_a_corrupt_journal_raises_at_open` FAILS with `Failed: DID NOT RAISE <class 'agent_manager.store.CorruptJournalError'>`, because `__init__` does not read the file today.
- `test_a_fresh_journal_on_an_existing_run_continues_after_the_last_line`, `test_a_resumed_store_continues_the_journal_sequence`, `test_a_journal_of_only_blank_lines_opens_at_zero` and `test_a_journal_opened_on_out_of_order_lines_continues_after_the_highest` PASS already. They are regression guards: today's disk-per-append code satisfies them, and the cached counter must keep satisfying them.

- [ ] **Step 4: Cache the counter in `__init__` and use it in `append`**

In `src/agent_manager/store.py`, replace `__init__` (L154-156):

```python
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.path = paths.run_dir(run_id) / JOURNAL_NAME
        self._seq = self.last_seq()
```

Replace the whole `append` method (L189-221):

```python
    def append(
        self,
        event: EventKind,
        payload: dict[str, Any],
        *,
        story: str | None = None,
        card: str | None = None,
        phase: str | None = None,
        attempt: int | None = None,
    ) -> JournalLine:
        """Append one line, flushed and fsynced before returning.

        One process writes a given run (P2). The sequence number is read from
        disk once, when the journal is opened, and cached; appending never
        re-reads the file. The cached number advances only once the line is
        on disk.
        """
        seq = self._seq + 1
        line = JournalLine(
            seq=seq,
            ts=datetime.now(timezone.utc),
            run_id=self.run_id,
            event=event,
            story=story,
            card=card,
            phase=phase,
            attempt=attempt,
            payload=payload,
        )
        text = json.dumps(line.model_dump(mode="json"), sort_keys=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(text + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self._seq = seq
        return line
```

Leave `last_seq()` (L158-162) and `read()` (L164-187) exactly as they are.

- [ ] **Step 5: Run the store tests to verify they pass**

Run: `uv run pytest tests/test_store.py -v`
Expected: every test PASSES, including the six added in Step 2, `test_a_reopened_journal_continues_the_sequence`, `test_a_run_directory_that_cannot_be_created_propagates_the_os_error` (the `OSError` still comes from `paths.run_dir` before `last_seq()` runs) and `test_rebuilding_a_run_with_no_journal_raises` (`last_seq()` returns 0 for a missing file, so opening does not raise; `read()` still raises `MissingJournalError`). `test_two_writers_on_one_run_never_reuse_a_sequence_number` no longer exists.

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest`
Expected: PASS, with no failures in `tests/test_engine.py`, `tests/test_dispatch.py`, `tests/test_cli.py` or `tests/e2e`.

- [ ] **Step 7: Commit**

```bash
git add src/agent_manager/store.py tests/test_store.py
git commit -m "$(cat <<'EOF'
Cache the journal's sequence number when it is opened

Journal reads the highest seq on disk once, in __init__, and append no
longer re-reads the file. A corrupt journal now raises at open.

Behaviour change: two Journal objects on one run are no longer
supported (P2: one process writes a run), so
test_two_writers_on_one_run_never_reuse_a_sequence_number is removed.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2UjNqoWcVtw4c7Kz7SgRZ
EOF
)"
```

---

### Task 2: Serialise appends behind a write lock

**Files:**
- Modify: `src/agent_manager/store.py:14-24` (imports), `src/agent_manager/store.py:151-221` (`class Journal`)
- Test: `tests/test_store.py:14-22` (imports), new tests after the Task 1 tests

**Interfaces:**
- Consumes: `Journal._seq: int` from Task 1; `Journal.append(...)`, `Journal.read()`, `Journal.path: Path`.
- Produces: `Journal._lock: threading.Lock`, held by `append` from numbering through `os.fsync`, and released on every exit path. Sibling 5657f0d4 will share one `Journal` between threads through `Store`; it relies on this lock and on nothing else here.

- [ ] **Step 1: Add the `threading` import to the tests**

In `tests/test_store.py`, change the stdlib imports (L14-17) to:

```python
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
```

- [ ] **Step 2: Write the failing tests**

In `tests/test_store.py`, insert after `test_opening_a_corrupt_journal_raises_at_open` (added in Task 1):

```python
def test_append_holds_the_lock_across_the_write_and_the_fsync(repo, monkeypatch):
    # Deterministic (P7): rather than racing threads and hoping to catch an
    # overlap, check the lock is held at the moment the line is fsynced.
    journal = store.Journal(RUN_ID)
    held_during_fsync: list[bool] = []
    real_fsync = store.os.fsync

    def spying_fsync(fd):
        held_during_fsync.append(journal._lock.locked())
        real_fsync(fd)

    monkeypatch.setattr(store.os, "fsync", spying_fsync)

    journal.append("run_upsert", {"i": 0})
    journal.append("run_upsert", {"i": 1})

    assert held_during_fsync == [True, True]
    assert journal._lock.locked() is False


def test_eight_threads_sharing_one_journal_write_800_whole_lines_numbered_1_to_800(repo):
    workers, per_worker = 8, 100
    journal = store.Journal(RUN_ID)
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
    journal = store.Journal(RUN_ID)
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


def test_an_invalid_line_releases_the_lock_and_does_not_spend_a_number(repo):
    # Review Focus 4: JournalLine validation runs inside the lock.
    journal = store.Journal(RUN_ID)
    journal.append("run_upsert", {"i": 0})

    with pytest.raises(ValidationError):
        journal.append("not_an_event", {})  # type: ignore[arg-type]
    assert journal._lock.locked() is False

    assert journal.append("run_upsert", {"i": 1}).seq == 2
    assert [line.seq for line in journal.read()] == [1, 2]
```

- [ ] **Step 3: Run the new tests to verify they fail**

Run: `uv run pytest tests/test_store.py -v -k "holds_the_lock or eight_threads or failed_write_releases or invalid_line_releases"`

Expected:
- `test_append_holds_the_lock_across_the_write_and_the_fsync`, `test_a_failed_write_releases_the_lock_and_does_not_spend_a_number` and `test_an_invalid_line_releases_the_lock_and_does_not_spend_a_number` FAIL with `AttributeError: 'Journal' object has no attribute '_lock'`.
- `test_eight_threads_sharing_one_journal_write_800_whole_lines_numbered_1_to_800` is expected to FAIL (duplicate seqs, because `self._seq + 1` and `self._seq = seq` are separated by an fsync that releases the GIL), but that race is not guaranteed on every run. The deterministic RED for the lock is `test_append_holds_the_lock_across_the_write_and_the_fsync`. Do not add sleeps to force the race.

- [ ] **Step 4: Add the lock**

In `src/agent_manager/store.py`, add `import threading` to the stdlib imports (L14-20), so they read:

```python
import json
import os
import sqlite3
import threading
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
```

Replace the `Journal` class docstring and `__init__` (L151-156, as left by Task 1):

```python
class Journal:
    """Append-only JSONL log for one run: the truth the projection is built from.

    One process writes a given run (P2), and its threads share one `Journal`.
    The highest sequence number on disk is read once, when the journal is
    opened, and cached; a lock serialises appends from those threads.
    """

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.path = paths.run_dir(run_id) / JOURNAL_NAME
        self._lock = threading.Lock()
        self._seq = self.last_seq()
```

Replace the whole `append` method (as left by Task 1):

```python
    def append(
        self,
        event: EventKind,
        payload: dict[str, Any],
        *,
        story: str | None = None,
        card: str | None = None,
        phase: str | None = None,
        attempt: int | None = None,
    ) -> JournalLine:
        """Append one line, flushed and fsynced before returning.

        One process writes a given run (P2). The sequence number is cached when
        the journal is opened, not re-read from disk, and the lock is held from
        numbering the line until it is fsynced, so the threads of that process
        never share a number or interleave their bytes. The cached number
        advances only once the line is on disk; if anything here raises, the
        lock is released and the next append retries the same number.
        """
        with self._lock:
            seq = self._seq + 1
            line = JournalLine(
                seq=seq,
                ts=datetime.now(timezone.utc),
                run_id=self.run_id,
                event=event,
                story=story,
                card=card,
                phase=phase,
                attempt=attempt,
                payload=payload,
            )
            text = json.dumps(line.model_dump(mode="json"), sort_keys=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(text + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            self._seq = seq
            return line
```

`last_seq()` and `read()` stay exactly as they are.

- [ ] **Step 5: Run the store tests to verify they pass**

Run: `uv run pytest tests/test_store.py -v`
Expected: every test PASSES, including the four added in this task and the six added in Task 1.

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest`
Expected: PASS, including `tests/e2e`. Sequential (`--max-concurrent 1`) runs are unaffected: one thread takes an uncontended lock per append.

- [ ] **Step 7: Commit**

```bash
git add src/agent_manager/store.py tests/test_store.py
git commit -m "$(cat <<'EOF'
Serialise journal appends behind a write lock

Journal.append holds a threading.Lock from numbering the line until it is
fsynced, and advances the cached counter only once the line is on disk.
Eight threads sharing one Journal now write 800 whole lines numbered 1..800.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2UjNqoWcVtw4c7Kz7SgRZ
EOF
)"
```

---

## Self-Review

1. Spec coverage:
   - Read the counter once at open, missing file = 0, `threading.Lock`: Task 1 Step 4 and Task 2 Step 4. The missing-file case is covered by the existing `test_append_writes_one_json_line_with_every_coordinate` (seq 1 on a fresh run).
   - `append` holds the lock across numbering, write, flush and fsync, and advances only on success: Task 2 Step 4, pinned by `test_append_holds_the_lock_across_the_write_and_the_fsync` and the two lock-release tests.
   - No re-read on append (spec test 2): `test_appending_reads_nothing_from_disk_once_the_journal_is_open`.
   - Docstring rewritten to "one process writes a given run": Task 1 Step 4, final wording in Task 2 Step 4.
   - `last_seq()` and `read()` unchanged: stated in both tasks.
   - Resumed run (spec test 3 plus `Store.open`): `test_a_fresh_journal_on_an_existing_run_continues_after_the_last_line`, `test_a_resumed_store_continues_the_journal_sequence`.
   - Corrupt journal raises at open: `test_opening_a_corrupt_journal_raises_at_open`.
   - Write or fsync failure releases the lock and does not advance: `test_a_failed_write_releases_the_lock_and_does_not_spend_a_number` covers the write side. The fsync side is implemented per spec and raised as an open question under Review Focus.
   - 8x100 stress test (spec test 1): `test_eight_threads_sharing_one_journal_write_800_whole_lines_numbered_1_to_800`.
   - Remove the obsolete test (spec test 4): Task 1 Step 1, reported in the Task 1 commit message.
   - Out-of-scope SQLite and `Store` locking: not touched (Global Constraints).
2. Placeholder scan: every code step carries full code; no TBD or "similar to" references.
3. Type consistency: `_seq: int` and `_lock: threading.Lock` are named the same in both tasks and all tests; `append`'s signature is unchanged; the tests use `_run` (L48) and `_append_raw` (L634), both existing helpers.
4. Review Focus: all five lines have a test in their owning task (1, 2 and 5 in Task 1; 3 and 4 in Task 2).

Pipeline note: the spec summary passed to the planner was cut off at 2000 characters (the upstream stage over-ran its brief). This plan was written from the spec on disk, not from that summary.
