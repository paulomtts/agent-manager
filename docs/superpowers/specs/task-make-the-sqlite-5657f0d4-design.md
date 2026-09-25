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
