<!-- task-pipeline: validated -->
# Make the SQLite projection thread-safe (card 5657f0d4)

Parent story: db70e86b "Make the store safe to share between threads". Milestone: cdbfa10d. This card narrows decision P2 of `docs/superpowers/specs/2026-09-24-parallel-stories-design.md` (lines 50-58) to the SQLite half of the store. The journal half (cached sequence and write lock in `Journal.append`) is done by sibling 1432e5cc, and this card does not touch `Journal` internals. It relies only on `Journal.append` handing out unique, contiguous sequence numbers under its own lock.

## Scope

All changes are in `src/agent_manager/store.py`, and all tests are in `tests/test_store.py`.

1. `open_db(root)`: open the connection with `check_same_thread=False` and an explicit busy timeout. Pass `timeout=` to `sqlite3.connect`, taken from a named module constant, so that `PRAGMA busy_timeout` reports it. Keep `row_factory = sqlite3.Row`, `PRAGMA journal_mode=WAL`, the idempotent schema and the commit. Update the docstring to say three things. The connection may be used from any thread of the one process. `Store` serialises that use. The timeout covers a reader in another process, such as `am status`, holding the database briefly. The docstring must not suggest that two `am` processes can write one run, since that is still unsupported (P2, RULE 3).
2. `Store.__init__` creates one `threading.RLock` (`self._lock`). The class docstring says the lock serialises every use of the shared connection. It also says that, for each `record_*`, the journal append and the row write form one critical section, so journal order equals row order.
3. `record_run`, `record_story`, `record_subtask`, `record_phase` and `record_attempt` each hold the lock across the whole method body. That covers the run-id check in `record_run`, the `Journal.append` and the `_write_*_row`. The journal line is still appended before the row is written (§9). If the row write raises, the line stays on disk, the exception propagates unchanged, and the `with` block releases the lock.
4. `Store.close` takes the lock, so the connection is not closed under an in-flight record.
5. Deliberate decision on the other connection users. `rebuild_from_journal` holds the lock across its whole body, from reading the journal through `_delete_run` and every row rewrite. No `record_*` can land between the delete and the rewrite, and the reentrant lock makes the nested `_write_*_row` calls safe. `_delete_run` is only called from there, so it is covered and takes no lock of its own. `Store.load_run` also holds the lock, so a read on the shared connection never interleaves with a write's execute or commit. The module-level `load_run(conn, run_id)`, `list_runs` and the CLI's own `open_db` connections are not changed.
6. Nothing outside the store takes this lock. It is never held around callers' work: no harness dispatch, git, board or file I/O beyond the journal append and the row write.

## Observable behaviour

- With one thread, behaviour is identical to today. Rows, journal lines, positions and the exceptions raised are the same, so `--max-concurrent 1` and the whole default suite, including `tests/e2e`, stay green (RULE 2).
- With many threads sharing one `Store`, no call raises `sqlite3.ProgrammingError` for a cross-thread connection or a "recursive use of cursors" error. The journal's `seq` values are unique and contiguous. Rebuilding the projection from the journal gives the same tree as the rows the threads wrote, including sibling `position` order.

## Error paths

- A failing row write, such as on a closed connection, still leaves its journal line on disk and releases the lock. `test_a_failed_sqlite_write_still_leaves_the_journal_line` keeps passing unchanged.
- A failing `Journal.append` writes no row and releases the lock. The journal's own retry-the-same-number semantics are unchanged.
- The `ValueError` from `record_run` for a mismatched run id is unchanged.

## Out of scope

Journal internals (1432e5cc), two processes on one repo or run, lanes, git worktree locks (P3), board locks and cooperative stop (P4), Integrate, per-story readiness, milestone-aware resume, watch/retry/cancel, cost capture, the reviewer's Plan-Hash brief, and Ctrl-C handling.

## Tests

The placement rule is §14 of the agent-manager design spec plus the CLAUDE.md layout: `tests/` mirrors `src/`. That puts every test below in the unit tier of `tests/test_store.py`, using the existing `repo` fixture and the `_run`, `_story`, `_subtask`, `_dispatch` and `RUN_ID` helpers. None of them goes in `tests/e2e`, and none needs a fake `claude`.

1. `test_open_db_connection_can_be_used_from_another_thread` (unit, `tests/test_store.py`). The connection from `open_db` runs a query on a second thread without raising. `PRAGMA busy_timeout` equals the module constant in milliseconds, and `PRAGMA journal_mode` is still `wal`.
2. `test_eight_threads_recording_through_one_store_agree_with_the_rebuilt_journal` (unit, `tests/test_store.py`). This is the stress test.
   - Setup, on the main thread: record the run, then several stories, because a child needs its parent. `_story()` has a fixed `card_id`, so build the distinct stories with `_story().model_copy(update={"card_id": ...})`. There is no phase or attempt helper: build `models.PhaseRun(name=..., kind="agent")` and `models.Attempt(n=..., dispatch=_dispatch(card, phase, n))` inline, and `_subtask(card_id)` for subtasks.
   - Each of 8 threads owns distinct subtasks spread across those stories. For each of its subtasks, a thread calls `record_subtask`, then `record_phase` for several phases, then `record_attempt` for several attempts per phase, with status transitions re-recorded. A subtask is therefore always recorded before its phases and attempts.
   - The threads start together on a `threading.Barrier(8)`. Each thread catches any exception into a shared list, and the main thread joins every thread.
   - Assert that the exception list is empty.
   - Assert that `[l.seq for l in st.journal.read()]` equals `list(range(1, N + 1))`, where N is the exact number of `record_*` calls made.
   - Read `before = st.load_run(RUN_ID)` from the database first. Then assert `st.rebuild_from_journal(RUN_ID) == before`, comparing whole pydantic models with no fields excluded, and `st.load_run(RUN_ID) == before`.
   - The test asserts only on results, never on timing or sleeps.
3. `test_a_failed_row_write_releases_the_store_lock` (unit, `tests/test_store.py`). After `record_story` raises `sqlite3.Error` on a closed store, another thread can acquire `st._lock` without blocking (`acquire(blocking=False)` returns `True`). The journal line is on disk.
4. Existing `test_a_failed_sqlite_write_still_leaves_the_journal_line` and every other test in `tests/test_store.py` and the rest of the default suite keep passing unmodified.

## Verification

`uv run pytest` passes in full.

---

# Make the SQLite Projection Thread-Safe Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the threads of one `am` process share one `Store`: `open_db` hands out a connection usable from any thread with an explicit busy timeout, and `Store` serialises every use of that connection behind one `threading.RLock`, so each journal append and its row write are one critical section.

**Architecture:** All production changes are in `src/agent_manager/store.py`. `open_db` gains `check_same_thread=False` and `timeout=BUSY_TIMEOUT_SECONDS` (new module constant). `Store.__init__` creates `self._lock = threading.RLock()`; `record_run`, `record_story`, `record_subtask`, `record_phase`, `record_attempt`, `close`, `load_run` and `rebuild_from_journal` each wrap their whole body in `with self._lock:`. `Journal` is not touched (sibling 1432e5cc owns it and it is already on this branch).

**Tech Stack:** Python >= 3.12, stdlib `sqlite3` and `threading`, Pydantic v2, pytest, run via `uv run pytest`.

**Spec:** `docs/superpowers/specs/task-make-the-sqlite-5657f0d4-design.md` (reproduced verbatim above).

## Global Constraints

- All production changes live in `src/agent_manager/store.py`; all tests live in `tests/test_store.py` (unit tier). Nothing goes in `tests/e2e`, and no test uses a fake `claude`.
- Do not modify `Journal` (class `Journal`, `store.py:152-239`) or any existing test.
- The journal is appended BEFORE the row is written (§9). This ordering is unchanged inside the lock.
- Keep `conn.row_factory = sqlite3.Row`, `PRAGMA journal_mode=WAL`, `conn.executescript(_SCHEMA)` and `conn.commit()` in `open_db`.
- The module-level `load_run(conn, run_id)`, `list_runs`, `latest_run_id` and the CLI's own `open_db` connections are not changed.
- The lock is never held around callers' work: only the journal append, the row writes, and reads on the shared connection.
- Nothing here supports two `am` processes writing one repo or run (P2, RULE 3); no docstring may suggest otherwise.
- Tests assert on results, never on timing or sleeps.
- `uv run pytest` (whole default suite, including `tests/e2e`) passes at the end of every task.

## Review Focus

1. A `Journal.append` that raises inside a `record_*` (disk full, fsync failure): the exception propagates unchanged, no row is written, and the store lock is free afterwards. Pinned by `test_a_failed_journal_append_releases_the_store_lock_and_writes_no_row` in Task 2.
2. `record_run` refusing a mismatched run id now raises from inside the `with` block: the `ValueError` is unchanged and the lock is free afterwards. Pinned by `test_a_refused_record_run_releases_the_store_lock` in Task 2.
3. `rebuild_from_journal` raising midway (corrupt journal line) must not leave the lock held, or every later `record_*` deadlocks. Pinned by `test_a_failed_rebuild_releases_the_store_lock` in Task 2.
4. A separate reader connection (what `am status` opens via `open_db`) must see exactly the tree the threads wrote once they are done, i.e. every write was committed. Pinned by the reader-connection assertion inside the Task 3 stress test.
5. A worker thread recording on a store another thread already closed gets a `sqlite3.Error` (not a hang, not a silent success), and the lock is free afterwards. Pinned by `test_recording_on_a_store_closed_by_another_thread_raises_and_frees_the_lock` in Task 2.

Caution for Task 3 (pre-existing, out of scope): `models.PhaseRun.detail` has no column in the `phases` table, so `load_run` always returns `detail=None`. The stress test must never set `detail`, or `rebuild_from_journal(...) == load_run(...)` fails for a reason unrelated to this card.

---

### Task 1: `open_db` returns a connection usable from any thread, with an explicit busy timeout

**Files:**
- Modify: `src/agent_manager/store.py:95-108` (add constant before `open_db`, change `sqlite3.connect` call and docstring)
- Test: `tests/test_store.py` (insert after `test_open_db_creates_the_project_file_in_wal_mode`, which ends at line 71)

**Interfaces:**
- Consumes: `paths.project_db_path(root)` (existing).
- Produces: `store.BUSY_TIMEOUT_SECONDS: float` (module constant, value `30.0`); `store.open_db(root: Path) -> sqlite3.Connection` whose connection has `check_same_thread=False` and `PRAGMA busy_timeout == int(BUSY_TIMEOUT_SECONDS * 1000)`.

- [ ] **Step 1: Write the failing test**

Insert into `tests/test_store.py` directly after `test_open_db_creates_the_project_file_in_wal_mode` (after line 71):

```python
def test_open_db_connection_can_be_used_from_another_thread(repo):
    # P2: the threads of one process share one connection, so open_db must not
    # pin it to the thread that opened it. The busy timeout is explicit and
    # WAL mode is kept.
    conn = store.open_db(repo)
    try:
        counts: list[int] = []
        errors: list[BaseException] = []

        def query() -> None:
            try:
                counts.append(conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0])
            except BaseException as error:  # surfaced by the assertion below
                errors.append(error)

        worker = threading.Thread(target=query)
        worker.start()
        worker.join()

        assert errors == []
        assert counts == [0]
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == int(
            store.BUSY_TIMEOUT_SECONDS * 1000
        )
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_store.py::test_open_db_connection_can_be_used_from_another_thread -v`
Expected: FAIL at `assert errors == []`, the list holding `sqlite3.ProgrammingError: SQLite objects created in a thread can only be used in that same thread...`.

- [ ] **Step 3: Write minimal implementation**

In `src/agent_manager/store.py`, replace the whole `open_db` function (lines 96-108) with the constant plus the new function:

```python
BUSY_TIMEOUT_SECONDS = 30.0
"""How long a statement on the projection waits for a lock held by another
connection before raising `sqlite3.OperationalError: database is locked`.

It exists for a reader in another process, such as `am status`, holding the
database briefly. It is not a licence for two `am` processes to write one run:
that is still unsupported (P2)."""


def open_db(root: Path) -> sqlite3.Connection:
    """Open the per-project projection, applying the schema idempotently.

    WAL mode is set before the schema so a reader never blocks the writer. Every
    `CREATE` is `IF NOT EXISTS`, so reopening an existing database neither
    destroys nor migrates what is already there.

    The connection may be used from any thread of the one process that writes a
    run (P2), so `check_same_thread` is off; `Store` serialises that use behind
    its own lock. `BUSY_TIMEOUT_SECONDS` covers a reader in another process,
    such as `am status`, holding the database briefly. Two `am` processes
    writing one run remain unsupported.
    """
    conn = sqlite3.connect(
        paths.project_db_path(root),
        timeout=BUSY_TIMEOUT_SECONDS,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_store.py::test_open_db_connection_can_be_used_from_another_thread -v`
Expected: PASS

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest`
Expected: all tests PASS (no existing test changed).

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/store.py tests/test_store.py
git commit -m "$(cat <<'EOF'
Let the projection connection be shared between threads

open_db now passes check_same_thread=False and an explicit busy timeout
(BUSY_TIMEOUT_SECONDS), keeping WAL mode and the idempotent schema.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2UjNqoWcVtw4c7Kz7SgRZ
EOF
)"
```

---

### Task 2: `Store` serialises every use of its connection behind one `RLock`

**Files:**
- Modify: `src/agent_manager/store.py` — `Store` class docstring and `__init__` (lines 474-483), `close` (501-502), `record_run` (506-518), `record_story` (520-527), `record_subtask` (529-537), `record_phase` (539-550), `record_attempt` (552-564), `Store.load_run` (736-743), `rebuild_from_journal` (747-775). Line numbers are as of the start of Task 2 plus the lines Task 1 added (about 16 lines further down); locate by method name.
- Test: `tests/test_store.py` (append at the end of the file)

**Interfaces:**
- Consumes: `store.open_db` from Task 1 (connection with `check_same_thread=False`), existing test helpers `_run(repo, run_id=RUN_ID)`, `_story()`, `_subtask(card_id, base="main")`, `_dispatch(card, phase, n)`, `_record_full_run(st, repo)`, `_append_raw(journal, record)`.
- Produces: `Store._lock: threading.RLock` (private attribute, read by tests only). New test helpers in `tests/test_store.py`: `_held_elsewhere(lock) -> bool` and class `_SpyingConnection(real: sqlite3.Connection, on_close: Callable[[], None])`.

- [ ] **Step 1: Write the failing tests**

Append to the end of `tests/test_store.py`:

```python
def _held_elsewhere(lock) -> bool:
    """True when a different thread cannot take `lock` right now.

    Probing from a second thread is what makes this meaningful for an RLock:
    the owning thread could always re-acquire it. Deterministic (P7): a
    non-blocking acquire either succeeds or it does not.
    """
    result: list[bool] = []

    def probe() -> None:
        acquired = lock.acquire(blocking=False)
        if acquired:
            lock.release()
        result.append(not acquired)

    prober = threading.Thread(target=probe)
    prober.start()
    prober.join()
    return result[0]


class _SpyingConnection:
    """Wraps a real connection so a test can observe the moment it is closed.

    `sqlite3.Connection.close` is read-only on the instance, so it cannot be
    monkeypatched directly; everything else is forwarded to the real one.
    """

    def __init__(self, real: sqlite3.Connection, on_close) -> None:
        self._real = real
        self._on_close = on_close

    def close(self) -> None:
        try:
            self._on_close()
        finally:
            self._real.close()

    def __getattr__(self, name: str):
        return getattr(self._real, name)


def test_every_record_holds_the_store_lock_across_the_journal_append_and_the_row_write(
    repo, monkeypatch
):
    # P2: the journal append and the row write are one critical section, so
    # journal order equals row order. Checked at the moment each happens.
    st = store.Store.open(repo, RUN_ID)
    seen: list[tuple[str, bool]] = []

    real_append = st.journal.append

    def spying_append(*args, **kwargs):
        seen.append(("journal", _held_elsewhere(st._lock)))
        return real_append(*args, **kwargs)

    monkeypatch.setattr(st.journal, "append", spying_append)

    for writer in (
        "_write_run_row",
        "_write_story_row",
        "_write_subtask_row",
        "_write_phase_row",
        "_write_attempt_row",
    ):
        real_writer = getattr(st, writer)

        def spying_writer(*args, _real=real_writer, _name=writer, **kwargs):
            seen.append((_name, _held_elsewhere(st._lock)))
            return _real(*args, **kwargs)

        monkeypatch.setattr(st, writer, spying_writer)

    try:
        st.record_run(_run(repo))
        st.record_story(_story())
        st.record_subtask("8831189b", _subtask())
        st.record_phase("8831189b", "ef248597", models.PhaseRun(name="implement", kind="agent"))
        st.record_attempt(
            "8831189b", "ef248597", "implement", models.Attempt(n=1, dispatch=_dispatch())
        )
        assert _held_elsewhere(st._lock) is False
    finally:
        st.close()

    assert seen == [
        ("journal", True),
        ("_write_run_row", True),
        ("journal", True),
        ("_write_story_row", True),
        ("journal", True),
        ("_write_subtask_row", True),
        ("journal", True),
        ("_write_phase_row", True),
        ("journal", True),
        ("_write_attempt_row", True),
    ]


def test_rebuild_and_load_run_hold_the_store_lock_on_the_shared_connection(
    repo, monkeypatch
):
    # Deliberate extension beyond the record_* methods: rebuild deletes and
    # rewrites rows on the shared connection, and load_run reads on it.
    st = store.Store.open(repo, RUN_ID)
    try:
        _record_full_run(st, repo)
        seen: list[tuple[str, bool]] = []

        real_read = st.journal.read

        def spying_read():
            seen.append(("read", _held_elsewhere(st._lock)))
            return real_read()

        monkeypatch.setattr(st.journal, "read", spying_read)

        real_delete = st._delete_run

        def spying_delete(run_id):
            seen.append(("_delete_run", _held_elsewhere(st._lock)))
            return real_delete(run_id)

        monkeypatch.setattr(st, "_delete_run", spying_delete)

        real_attempt_writer = st._write_attempt_row

        def spying_attempt_writer(*args, **kwargs):
            seen.append(("_write_attempt_row", _held_elsewhere(st._lock)))
            return real_attempt_writer(*args, **kwargs)

        monkeypatch.setattr(st, "_write_attempt_row", spying_attempt_writer)

        real_load_run = store.load_run

        def spying_load_run(conn, run_id):
            seen.append(("load_run", _held_elsewhere(st._lock)))
            return real_load_run(conn, run_id)

        monkeypatch.setattr(store, "load_run", spying_load_run)

        rebuilt = st.rebuild_from_journal(RUN_ID)
        loaded = st.load_run(RUN_ID)
        assert _held_elsewhere(st._lock) is False
    finally:
        st.close()

    assert rebuilt == loaded
    assert seen == [
        ("read", True),
        ("_delete_run", True),
        ("_write_attempt_row", True),
        ("_write_attempt_row", True),
        ("load_run", True),
    ]


def test_close_holds_the_store_lock(repo):
    # The connection is never closed under an in-flight record.
    st = store.Store.open(repo, RUN_ID)
    held: list[bool] = []
    st._conn = _SpyingConnection(st._conn, lambda: held.append(_held_elsewhere(st._lock)))

    st.close()

    assert held == [True]
    assert _held_elsewhere(st._lock) is False


def test_a_failed_row_write_releases_the_store_lock(repo):
    # §9: the journal line survives the failed row write, and the `with` block
    # releases the lock so the next record is not deadlocked.
    st = store.Store.open(repo, RUN_ID)
    st.record_run(_run(repo))
    st.close()

    with pytest.raises(sqlite3.Error):
        st.record_story(_story())

    assert _held_elsewhere(st._lock) is False
    lines = store.Journal(RUN_ID).read()
    assert [line.event for line in lines] == ["run_upsert", "story_upsert"]


def test_a_failed_journal_append_releases_the_store_lock_and_writes_no_row(
    repo, monkeypatch
):
    # Review Focus 1: the journal is written first, so when it fails there is
    # no row, the same exception propagates, and the lock is free.
    st = store.Store.open(repo, RUN_ID)
    try:
        st.record_run(_run(repo))

        def failing_append(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(st.journal, "append", failing_append)

        with pytest.raises(OSError, match="disk full"):
            st.record_story(_story())

        assert _held_elsewhere(st._lock) is False
        assert st.connection.execute("SELECT COUNT(*) FROM stories").fetchone()[0] == 0
    finally:
        st.close()


def test_a_refused_record_run_releases_the_store_lock(repo):
    # Review Focus 2: the run-id check now runs inside the lock.
    st = store.Store.open(repo, RUN_ID)
    try:
        with pytest.raises(ValueError):
            st.record_run(_run(repo, run_id="run-somewhere-else"))
        assert _held_elsewhere(st._lock) is False
    finally:
        st.close()


def test_a_failed_rebuild_releases_the_store_lock(repo):
    # Review Focus 3: a rebuild that raises must not leave every later record
    # deadlocked behind it.
    st = store.Store.open(repo, RUN_ID)
    try:
        _record_full_run(st, repo)
        with st.journal.path.open("a", encoding="utf-8") as handle:
            handle.write("{not json at all\n")

        with pytest.raises(store.CorruptJournalError):
            st.rebuild_from_journal(RUN_ID)

        assert _held_elsewhere(st._lock) is False
    finally:
        st.close()


def test_recording_on_a_store_closed_by_another_thread_raises_and_frees_the_lock(repo):
    # Review Focus 5: a worker that records after another thread closed the
    # store gets a sqlite3.Error, not a hang or a silent success.
    st = store.Store.open(repo, RUN_ID)
    st.record_run(_run(repo))
    st.close()

    errors: list[BaseException] = []

    def work() -> None:
        try:
            st.record_story(_story())
        except BaseException as error:  # inspected below
            errors.append(error)

    worker = threading.Thread(target=work)
    worker.start()
    worker.join()

    assert len(errors) == 1
    assert isinstance(errors[0], sqlite3.Error)
    assert _held_elsewhere(st._lock) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_store.py -v -k "store_lock or holds_the_store_lock or closed_by_another_thread"`
Expected: all 8 new tests FAIL with `AttributeError: 'Store' object has no attribute '_lock'`.

- [ ] **Step 3: Add the lock and the class docstring**

In `src/agent_manager/store.py`, replace the `Store` class docstring and `__init__` (currently):

```python
class Store:
    """The two stores of D5, bound together by the write ordering of §9.

    Every `record_*` appends the journal line first and writes the row second.
    There is deliberately no public method that writes a row on its own.
    """

    def __init__(self, conn: sqlite3.Connection, journal: Journal) -> None:
        self._conn = conn
        self._journal = journal
```

with:

```python
class Store:
    """The two stores of D5, bound together by the write ordering of §9.

    Every `record_*` appends the journal line first and writes the row second.
    There is deliberately no public method that writes a row on its own.

    One process writes a given run (P2), and its threads share one `Store`. A
    single re-entrant lock serialises every use of the shared connection. Each
    `record_*` holds it across the journal append and the row write, so the two
    are one critical section and journal order equals row order; `close`,
    `load_run` and `rebuild_from_journal` hold it too. The lock never covers the
    caller's own work, only the append and the row write.
    """

    def __init__(self, conn: sqlite3.Connection, journal: Journal) -> None:
        self._conn = conn
        self._journal = journal
        self._lock = threading.RLock()
```

- [ ] **Step 4: Lock `close` and every `record_*`**

Replace `close` and the five `record_*` methods (from `def close(self) -> None:` through the end of `record_attempt`, just before `# -- row writers ---`) with:

```python
    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- recording ---------------------------------------------------------
    #
    # Each method holds the store lock across its whole body: the journal line
    # is appended first and the row written second (§9), with no other record
    # able to land in between. If the row write raises, the line stays on disk,
    # the exception propagates unchanged and the `with` block releases the lock.

    def record_run(self, run: models.Run) -> JournalLine:
        with self._lock:
            if run.id != self.run_id:
                raise ValueError(
                    f"store is bound to run {self.run_id!r} but was handed run"
                    f" {run.id!r}: the row is keyed by the store's id while the"
                    " journal payload keeps the model's, so the two stores would"
                    " disagree about which run this is"
                )
            line = self._journal.append(
                "run_upsert", run.model_dump(mode="json", exclude={"stories"})
            )
            self._write_run_row(self.run_id, run)
            return line

    def record_story(self, story: models.StoryRun) -> JournalLine:
        with self._lock:
            line = self._journal.append(
                "story_upsert",
                story.model_dump(mode="json", exclude={"subtasks"}),
                story=story.card_id,
            )
            self._write_story_row(self.run_id, story)
            return line

    def record_subtask(self, story_id: str, subtask: models.SubtaskRun) -> JournalLine:
        with self._lock:
            line = self._journal.append(
                "subtask_upsert",
                subtask.model_dump(mode="json", exclude={"phases"}),
                story=story_id,
                card=subtask.card_id,
            )
            self._write_subtask_row(self.run_id, story_id, subtask)
            return line

    def record_phase(
        self, story_id: str, card_id: str, phase: models.PhaseRun
    ) -> JournalLine:
        with self._lock:
            line = self._journal.append(
                "phase_upsert",
                phase.model_dump(mode="json", exclude={"attempts"}),
                story=story_id,
                card=card_id,
                phase=phase.name,
            )
            self._write_phase_row(self.run_id, story_id, card_id, phase)
            return line

    def record_attempt(
        self, story_id: str, card_id: str, phase_name: str, attempt: models.Attempt
    ) -> JournalLine:
        with self._lock:
            line = self._journal.append(
                "attempt_upsert",
                attempt.model_dump(mode="json"),
                story=story_id,
                card=card_id,
                phase=phase_name,
                attempt=attempt.n,
            )
            self._write_attempt_row(
                self.run_id, story_id, card_id, phase_name, attempt
            )
            return line
```

- [ ] **Step 5: Lock `Store.load_run` and `rebuild_from_journal`**

Replace `Store.load_run` and `rebuild_from_journal` (from `def load_run(self, run_id: str)` through `return run` at the end of `rebuild_from_journal`; leave `_delete_run` unchanged) with:

```python
    def load_run(self, run_id: str) -> models.Run | None:
        """The module-level `load_run` over this store's own connection.

        Kept as a method because `rebuild_from_journal` and every existing caller
        already hold a `Store`; the free function is what a reader without a run
        id uses. Holds the store lock so a read on the shared connection never
        interleaves with a write's execute or commit.
        """
        with self._lock:
            return load_run(self._conn, run_id)

    # -- rebuild -------------------------------------------------------------

    def rebuild_from_journal(self, run_id: str) -> models.Run:
        """Replace this run's projection with what its journal says (D5).

        The journal wins: every row for `run_id` is deleted and rewritten from
        the replayed tree, so the result is the same whether the projection was
        stale, truncated or already correct.

        The store lock is held from reading the journal through the delete and
        every rewrite, so no `record_*` lands between the delete and the
        rewrite. `_delete_run` is only called from here and takes no lock of
        its own.
        """
        with self._lock:
            journal = (
                self._journal if self._journal.run_id == run_id else Journal(run_id)
            )
            run = replay(journal.read())
            if run.id != run_id:
                raise JournalError(
                    f"journal of run {run_id!r} has a run_upsert naming run"
                    f" {run.id!r}: refusing to key its projection under two ids"
                )
            self._delete_run(run_id)
            self._write_run_row(run_id, run)
            for story in run.stories:
                self._write_story_row(run_id, story)
                for subtask in story.subtasks:
                    self._write_subtask_row(run_id, story.card_id, subtask)
                    for phase in subtask.phases:
                        self._write_phase_row(
                            run_id, story.card_id, subtask.card_id, phase
                        )
                        for attempt in phase.attempts:
                            self._write_attempt_row(
                                run_id,
                                story.card_id,
                                subtask.card_id,
                                phase.name,
                                attempt,
                            )
            return run
```

- [ ] **Step 6: Run the new tests to verify they pass**

Run: `uv run pytest tests/test_store.py -v -k "store_lock or holds_the_store_lock or closed_by_another_thread"`
Expected: all 8 PASS.

- [ ] **Step 7: Run the full suite**

Run: `uv run pytest`
Expected: all tests PASS, including the unmodified `test_a_failed_sqlite_write_still_leaves_the_journal_line` and `test_rebuild_picks_up_a_journal_line_whose_row_never_landed`, and everything under `tests/e2e`.

- [ ] **Step 8: Commit**

```bash
git add src/agent_manager/store.py tests/test_store.py
git commit -m "$(cat <<'EOF'
Serialise the store's shared connection behind one RLock

Every record_* holds the lock across the journal append and the row write,
so journal order equals row order. close, load_run and rebuild_from_journal
take it too. The journal is still appended first and survives a failed row
write.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2UjNqoWcVtw4c7Kz7SgRZ
EOF
)"
```

---

### Task 3: Eight threads recording through one `Store` agree with the rebuilt journal

**Files:**
- Test: `tests/test_store.py` (append at the end of the file)
- No production change.

**Interfaces:**
- Consumes: `store.Store.open(root, run_id)`, `Store.record_run/record_story/record_subtask/record_phase/record_attempt`, `Store.load_run(run_id)`, `Store.rebuild_from_journal(run_id)`, `Store.journal.read()`, `store.open_db(root)`, module-level `store.load_run(conn, run_id)`; test helpers `_run`, `_story`, `_subtask`, `_dispatch`, `RUN_ID`.
- Produces: test-module constants `STRESS_WORKERS = 8`, `STRESS_STORIES = 4`, `STRESS_SUBTASKS_PER_WORKER = 3`, `STRESS_PHASES = ("explore", "implement")`, `STRESS_ATTEMPTS_PER_PHASE = 2`, and helper `_record_one_subtask(st, story_id, card_id) -> int`.

Note on RED: this is the spec's acceptance test for the observable behaviour. Its deterministic RED guards are Task 1's cross-thread test and Task 2's lock-held tests; a race-driven stress test cannot be made to fail deterministically against the pre-lock code, so it is expected to PASS on first run here. Do not add sleeps or timing assertions to force a failure.

- [ ] **Step 1: Write the test**

Append to the end of `tests/test_store.py`:

```python
STRESS_WORKERS = 8
STRESS_STORIES = 4
STRESS_SUBTASKS_PER_WORKER = 3
STRESS_PHASES = ("explore", "implement")
STRESS_ATTEMPTS_PER_PHASE = 2


def _record_one_subtask(st: store.Store, story_id: str, card_id: str) -> int:
    """Record one subtask's whole life, each parent before its children.

    Returns how many `record_*` calls it made, so the caller can check the
    journal holds exactly that many lines. `PhaseRun.detail` is left unset on
    purpose: the projection has no column for it, so setting it would make the
    rebuilt tree differ from the rows for a reason unrelated to threading.
    """
    calls = 0
    subtask = _subtask(card_id)
    st.record_subtask(story_id, subtask)
    calls += 1
    for name in STRESS_PHASES:
        phase = models.PhaseRun(name=name, kind="agent", status="started")
        st.record_phase(story_id, card_id, phase)
        calls += 1
        for n in range(1, STRESS_ATTEMPTS_PER_PHASE + 1):
            attempt = models.Attempt(n=n, dispatch=_dispatch(card_id, name, n))
            st.record_attempt(story_id, card_id, name, attempt)
            calls += 1
            st.record_attempt(
                story_id,
                card_id,
                name,
                attempt.model_copy(update={"status": "ok", "exit_code": 0}),
            )
            calls += 1
        st.record_phase(story_id, card_id, phase.model_copy(update={"status": "done"}))
        calls += 1
    st.record_subtask(story_id, subtask.model_copy(update={"status": "done"}))
    calls += 1
    return calls


def test_eight_threads_recording_through_one_store_agree_with_the_rebuilt_journal(repo):
    # P2: the threads of one process share one Store. Deterministic (P7): the
    # assertions are on results only -- the journal's numbering and the tree
    # the rows and the journal each produce -- never on timing.
    st = store.Store.open(repo, RUN_ID)
    story_ids = [f"story-{i}" for i in range(STRESS_STORIES)]
    try:
        # A child needs its parent: the run and every story exist before any
        # thread starts.
        st.record_run(_run(repo))
        for story_id in story_ids:
            st.record_story(_story().model_copy(update={"card_id": story_id}))
        setup_calls = 1 + STRESS_STORIES

        start = threading.Barrier(STRESS_WORKERS)
        errors: list[BaseException] = []
        calls = [0] * STRESS_WORKERS

        def work(worker: int) -> None:
            try:
                start.wait(timeout=30)
                for t in range(STRESS_SUBTASKS_PER_WORKER):
                    # Spread each worker's subtasks across the stories, so every
                    # story gets siblings recorded by several threads at once.
                    index = worker * STRESS_SUBTASKS_PER_WORKER + t
                    story_id = story_ids[index % STRESS_STORIES]
                    calls[worker] += _record_one_subtask(st, story_id, f"w{worker}-t{t}")
            except BaseException as error:  # surfaced by the assertion below
                errors.append(error)

        threads = [
            threading.Thread(target=work, args=(worker,))
            for worker in range(STRESS_WORKERS)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=120)

        assert [thread.is_alive() for thread in threads] == [False] * STRESS_WORKERS
        assert errors == []

        per_subtask = (
            1 + len(STRESS_PHASES) * (1 + 2 * STRESS_ATTEMPTS_PER_PHASE + 1) + 1
        )
        total = setup_calls + sum(calls)
        assert total == setup_calls + (
            STRESS_WORKERS * STRESS_SUBTASKS_PER_WORKER * per_subtask
        )
        assert [line.seq for line in st.journal.read()] == list(range(1, total + 1))

        # Read the rows BEFORE rebuilding: the rebuild rewrites them.
        before = st.load_run(RUN_ID)
        assert before is not None
        assert [story.card_id for story in before.stories] == story_ids
        recorded = [subtask for story in before.stories for subtask in story.subtasks]
        assert sorted(subtask.card_id for subtask in recorded) == sorted(
            f"w{worker}-t{t}"
            for worker in range(STRESS_WORKERS)
            for t in range(STRESS_SUBTASKS_PER_WORKER)
        )
        for subtask in recorded:
            assert subtask.status == "done"
            assert [phase.name for phase in subtask.phases] == list(STRESS_PHASES)
            for phase in subtask.phases:
                assert phase.status == "done"
                assert [a.n for a in phase.attempts] == list(
                    range(1, STRESS_ATTEMPTS_PER_PHASE + 1)
                )
                assert all(a.status == "ok" and a.exit_code == 0 for a in phase.attempts)

        # Review Focus 4: a separate reader connection, as `am status` opens,
        # sees exactly what the threads committed.
        reader = store.open_db(repo)
        try:
            assert store.load_run(reader, RUN_ID) == before
        finally:
            reader.close()

        assert st.rebuild_from_journal(RUN_ID) == before
        assert st.load_run(RUN_ID) == before
    finally:
        st.close()
```

- [ ] **Step 2: Run the test**

Run: `uv run pytest tests/test_store.py::test_eight_threads_recording_through_one_store_agree_with_the_rebuilt_journal -v`
Expected: PASS. If it fails, do not weaken any assertion: a mismatch between `before` and the rebuilt tree means sibling `position` order diverged from journal order, i.e. a `record_*` is not holding `self._lock` across both its append and its row write; fix `store.py`, not the test.

- [ ] **Step 3: Run it several more times to check it is stable**

Run: `uv run pytest tests/test_store.py::test_eight_threads_recording_through_one_store_agree_with_the_rebuilt_journal -v` three more times.
Expected: PASS every time.

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest`
Expected: all tests PASS, including `tests/e2e`.

- [ ] **Step 5: Commit**

```bash
git add tests/test_store.py
git commit -m "$(cat <<'EOF'
Stress one shared Store from eight threads

Eight threads record subtasks, phases and attempts across four shared
stories; the journal is numbered 1..N and the rows, a separate reader
connection and the rebuilt journal all agree as whole models.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2UjNqoWcVtw4c7Kz7SgRZ
EOF
)"
```

---

## Self-Review

- Spec coverage: Scope 1 (open_db, constant, docstring) -> Task 1. Scope 2 (RLock, class docstring) -> Task 2 Step 3. Scope 3 (record_* whole-body lock, journal-first) -> Task 2 Step 4, test `test_every_record_holds_the_store_lock_across_the_journal_append_and_the_row_write`. Scope 4 (close) -> Task 2 Step 4, `test_close_holds_the_store_lock`. Scope 5 (rebuild and Store.load_run locked; module-level functions unchanged) -> Task 2 Step 5, `test_rebuild_and_load_run_hold_the_store_lock_on_the_shared_connection`. Scope 6 (never around callers' work) -> no code outside `store.py` touches the lock. Error paths -> `test_a_failed_row_write_releases_the_store_lock`, `test_a_failed_journal_append_releases_the_store_lock_and_writes_no_row`, `test_a_refused_record_run_releases_the_store_lock`, plus the unchanged existing tests. Spec tests 1-3 -> Tasks 1, 3, 2 respectively; spec test 4 -> full-suite steps.
- Placeholder scan: none; every code step carries full code.
- Type consistency: `BUSY_TIMEOUT_SECONDS`, `Store._lock`, `_held_elsewhere`, `_SpyingConnection`, `_record_one_subtask` and the `STRESS_*` constants are used with the same names and signatures throughout.
- Review Focus: five lines, each pinned by a named test in Task 2 or Task 3.
