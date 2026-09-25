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
- If validation, the open or the write fails inside `append`, the exception propagates and the lock is released. The cached counter does not advance, so the next append retries the same number. If only the fsync fails, the line is already in the file, so the counter does advance: retrying the number would put a duplicate seq on disk (resolved at review).

## Deliberate change: an obsolete test

`test_two_writers_on_one_run_never_reuse_a_sequence_number` (`tests/test_store.py` L162-173) creates two `Journal` objects on one run before any append and expects the sequence [1,2,3]. Its comment names the old "derived from disk at append time, not cached" behaviour. With the counter cached, both instances start at 0 and the test must fail. Reading the counter lazily at the first append would also fail it. This test contradicts P2 and this card, so this card removes it on purpose, and the removal must be reported as a behaviour change. Later stages must not keep it passing by re-reading the disk on each append, because that defeats the card. The other existing store tests, including `test_a_reopened_journal_continues_the_sequence` (L151), must pass unchanged.

## Tests

Test-placement rule: `CLAUDE.md` says tests mirror `src/` under `tests/`, and main spec section 14 unit-tests store and file logic against temp dirs, keeping e2e as a separate opt-in tier. P7 requires determinism, not timing. All the tests below therefore go in `tests/test_store.py` (the flat mirror of `store.py`) and use the existing `repo` fixture and `RUN_ID`. None go in `tests/e2e` or a new tier.

1. `tests/test_store.py`: 8 threads each append 100 times through one shared `Journal`. The file has exactly 800 non-blank lines, each line passes `json.loads` (no interleaved writes), and the sequence numbers are exactly {1..800}, with no duplicates or gaps.
2. `tests/test_store.py`: use monkeypatch to count calls to `Journal.read` and `Journal.last_seq` (or file opens for reading). After the journal is constructed, appending N lines (for example N=50) makes zero further reads. The test counts calls and does not time anything.
3. `tests/test_store.py`: append some lines, construct a fresh `Journal(RUN_ID)` without calling `last_seq()` first, and check that its first append gets `last + 1` and the file's sequence numbers stay contiguous.
4. Remove `test_two_writers_on_one_run_never_reuse_a_sequence_number` as described above.

Verification: the whole default suite, including `tests/e2e`, passes under `uv run pytest`, and `--max-concurrent 1` behaviour is unchanged.
